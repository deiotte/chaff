"""Credentials in specs: find them, refuse them, resolve them (ADR-0027).

A spec is a *shareable document* — saved to the library, listed over the API,
committed to a repo, attached to a support bundle. An external assessment
(F-05) found `/library` handing back a stored `Authorization: Bearer …`
verbatim, so anyone who could read the endpoint or the file got a reusable
credential.

Three pieces, in one place so they can't disagree about what counts:

1. `find_credentials` — the dotted paths of secret-bearing values in a spec.
2. `redact` — the same paths removed, for reading specs saved before the rule
   existed.
3. `resolve_env` — turns `"${VAR}"` into the environment's value at run time,
   which is what makes refusing a literal *usable*: an authenticated sink is
   still expressible, just without the secret on disk.

Deliberately a curated list, not a heuristic. `options.key` is Kafka's static
*message* key, and "contains the substring key" would have broken it — the
kind of false positive that teaches people to work around the check.
"""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

# Option names whose value is a secret. Note what's absent: `username` and
# `sasl.username` are half a credential, not one, and blocking them would stop
# people saving otherwise-shareable specs for no security gain.
CREDENTIAL_OPTION_KEYS = frozenset({
    "password", "passwd", "secret", "token", "access_token", "auth_token",
    "api_key", "apikey", "credential", "credentials", "passphrase",
    "private_key", "client_secret",
})

# Request headers that carry one.
CREDENTIAL_HEADER_NAMES = frozenset({
    "authorization", "proxy-authorization", "cookie",
    "x-api-key", "x-auth-token", "api-key",
})

# Kafka's `options.config` is a passthrough to confluent-kafka, so its keys
# are librdkafka's, not ours — matched by pattern rather than an exact list.
_CONFIG_SECRET = re.compile(
    r"(password|secret|\.key$|keytab|passphrase)", re.IGNORECASE)

# `${VAR}` — the saveable stand-in for a secret.
_ENV_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

ENV_SUGGESTIONS = {
    "sink.options.password": "CHAFF_MQTT_PASSWORD",
    "sink.options.username": "CHAFF_MQTT_USERNAME",
}


class CredentialInSpec(ValueError):
    """A spec carries a secret where only a reference belongs."""


def is_env_placeholder(value: Any) -> bool:
    """True for `"${VAR}"` — a reference, not a secret, so it may be saved."""
    return isinstance(value, str) and bool(_ENV_PLACEHOLDER.match(value.strip()))


def _url_has_password(value: Any) -> bool:
    """A credential-bearing URL: `https://user:pass@host`."""
    if not isinstance(value, str):
        return False
    try:
        return bool(urlsplit(value).password)
    except ValueError:
        return False


def find_credentials(spec: dict[str, Any]) -> list[str]:
    """Dotted paths of literal secrets in `spec`. `${VAR}` refs don't count."""
    found: list[str] = []
    options = ((spec.get("sink") or {}).get("options") or {})
    if not isinstance(options, dict):
        return found

    for name, value in options.items():
        path = f"sink.options.{name}"
        if name.lower() in CREDENTIAL_OPTION_KEYS and value not in (None, ""):
            if not is_env_placeholder(value):
                found.append(path)
        elif name == "url" and _url_has_password(value):
            found.append(f"{path} (password embedded in the URL)")

    headers = options.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            if name.lower() in CREDENTIAL_HEADER_NAMES and value not in (None, ""):
                if not is_env_placeholder(value):
                    found.append(f"sink.options.headers.{name}")

    config = options.get("config")
    if isinstance(config, dict):
        for name, value in config.items():
            if _CONFIG_SECRET.search(str(name)) and value not in (None, ""):
                if not is_env_placeholder(value):
                    found.append(f"sink.options.config.{name}")

    return found


def reject_credentials(spec: dict[str, Any]) -> None:
    """Raise `CredentialInSpec` if the spec carries a literal secret.

    The message has to be actionable: it names every offending field and the
    exact replacement, because "your spec has a secret in it" leaves someone
    guessing at what a spec is even allowed to contain.
    """
    found = find_credentials(spec)
    if not found:
        return
    lines = []
    for path in found:
        field = path.split(" (")[0]
        # No CHAFF_ prefix on the generic suggestion: `${VAR}` reads whatever
        # name the operator picks, so it is *their* variable, not a chaff
        # setting. (It also stops the deployment guard in
        # tests/test_deployment_config.py reading this placeholder as a
        # setting the app forgot to forward — which it did, correctly.)
        suggested = ENV_SUGGESTIONS.get(field, "MY_SINK_TOKEN")
        lines.append(f"  - {path}\n      replace the value with \"${{{suggested}}}\" "
                     f"and set {suggested} in the environment")
    raise CredentialInSpec(
        "this spec contains credentials, which are not saved to the library "
        "(a saved spec is a shareable document — anyone who can read it would "
        "get a working credential):\n" + "\n".join(lines))


def redact(spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """`(spec without its secrets, the paths removed)`.

    For specs saved before the rule existed. Values are *removed* rather than
    replaced with a placeholder so the result can be saved again — a
    "***REDACTED***" string under a `password` key would itself be refused,
    which would strand the spec.
    """
    paths = find_credentials(spec)
    if not paths:
        return spec, []

    import copy

    out = copy.deepcopy(spec)
    options = out["sink"]["options"]
    for path in paths:
        field = path.split(" (")[0]
        parts = field.split(".")[2:]  # strip "sink.options"
        target = options
        for part in parts[:-1]:
            target = target.get(part, {})
        target.pop(parts[-1], None)
    return out, paths


def resolve_env(options: dict[str, Any]) -> dict[str, Any]:
    """Substitute `"${VAR}"` with the environment's value, recursively.

    This is what makes the refusal usable rather than merely restrictive: an
    authenticated sink stays expressible, with the secret supplied at run time
    instead of stored. An unset variable is left as-is so the sink fails with
    its own error naming the destination, rather than silently sending an
    empty credential and getting an opaque 401.
    """
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            match = _ENV_PLACEHOLDER.match(value.strip())
            if match:
                return os.environ.get(match.group(1), value)
        return value

    return walk(options)
