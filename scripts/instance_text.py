#!/usr/bin/env python3
"""Free-text detectors shared by instance validation and the public scrub.

Finds IPv4 and IPv6 addresses, domains, home or user paths and host-shaped
names. Stdlib only. Private-looking pattern pieces are joined so this file
passes the repo's own public scan.
"""
from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path

SCRUB_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}
IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w]|\.\d)")
IPV6 = re.compile(r"(?<![\w:.])[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])")
DOTTED = re.compile(r"(?<![\w.-])[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
HOME_PATH = re.compile("|".join((
    r"(?<![\w.])/" + r"home/",
    r"(?<![\w.])/var/" + r"home/",
    r"(?<![\w.])/" + r"Users/",
    r"(?<![\w.])/" + r"root/",
    r"(?<![\w.])/" + r"srv/[\w.-]+/[\w.-]+",
    r"(?<![\w~])~[A-Za-z0-9_-]*/",
    r"\b[A-Za-z]:\\" + r"Users\\",
)))
HOST_TOKEN = re.compile(r"[a-z]{2,}[0-9]{2,}")  # one letter (f00, T00) is hex or a timestamp
HOST_SHAPED_ALLOW = {"sha256", "sha384", "sha512", "base64", "utf16", "utf32", "x86", "int32", "int64",
                     "float32", "float64", "http11", "amd64", "arm64", "aarch64", "win32", "win64"}
# Percentiles, RFC numbers, Python and wheel tags, OS and runtime versions, top-k and ndcg@k, ES targets.
HOST_SHAPED_EXEMPT = re.compile(
    r"p[0-9]{2,3}|rfc[0-9]+|py3[0-9]+|cp3[0-9]+|ubuntu[0-9]{4}|node[0-9]{2}|top[0-9]+|ndcg[0-9]+|es20[0-9]{2}")
HEX_ID = re.compile(r"[0-9a-f]{8,}")  # digests and card ids are not host names
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
# Credential-shaped values. Pieces are joined so no file here looks like a credential.
CREDENTIAL = re.compile("|".join((
    r"(?<![\w-])s" + r"k-(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{20,}",
    r"\bgh[pousr]" + r"_[A-Za-z0-9]{20,}",
    r"\bgithub" + r"_pat_",
    r"\bxox[abp]" + r"-",
    r"\bAK" + r"IA[0-9A-Z]{16}\b",
    r"-----" + r"BEGIN",
)))
URL_CREDENTIAL = re.compile(r"://[^\s/:@]+:[^\s/@]+@([^\s/:]*)")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
DOCUMENTATION_NETS = tuple(ipaddress.ip_network(net) for net in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32"))
# A dotted token is a domain when its last label is one of these public or
# private-network TLDs. A closed list, not "any letters", so dotted metric names,
# event types (review.started) and file names (cluster.yaml) are not domains.
# Real estate names with an exotic TLD are caught by SKRSI_ESTATE_DENYLIST.
DOMAIN_TLDS = {
    "com", "net", "org", "edu", "gov", "mil", "int", "io", "ai", "app", "dev", "co", "me", "tv", "cc",
    "biz", "info", "xyz", "tech", "cloud", "online", "site", "world", "network", "systems", "club",
    "it", "uk", "us", "de", "eu", "fr", "nl", "ch", "es", "se", "dk", "fi", "pl", "cz", "ru", "cn", "jp",
    "kr", "br", "mx", "ca", "au", "nz", "ie", "pt", "gr", "hu", "ro", "sk", "si", "hr", "ua", "tr", "il",
    "za", "ar", "cl", "sg", "hk", "tw", "vn", "th", "ly", "gg", "je", "im", "ws", "fm", "gl",
    "local", "lan", "internal", "home", "arpa", "localdomain", "corp", "intranet", "private",
}
# Public domains an instance may name: the code host, the schema dialect, the
# framework site, and names reserved for documentation (RFC 2606).
PUBLIC_DOMAINS = ("github.com", "githubusercontent.com", "json-schema.org", "smilintux.github.io",
                  "example.com", "example.org", "example.net")


def is_domain(token: str) -> bool:
    labels = token.lower().rstrip(".").split(".")
    if len(labels) < 2 or labels[-1] not in DOMAIN_TLDS or not all(LABEL.fullmatch(label) for label in labels):
        return False
    name = ".".join(labels)
    return not any(name == public or name.endswith("." + public) for public in PUBLIC_DOMAINS)


def _addresses(text: str):
    """Every dotted quad, even one ipaddress rejects (leading zeros); and IPv6."""
    for match in IPV4.finditer(text):
        octets = [int(part) for part in match.group(0).split(".")]
        address = ipaddress.IPv4Address(".".join(map(str, octets))) if max(octets) <= 255 else None
        yield "IPv4", match.group(0), address
    for match in IPV6.finditer(text):
        if not re.search(r"[0-9A-Fa-f]", match.group(0)):
            continue
        try:
            yield "IPv6", match.group(0), ipaddress.IPv6Address(match.group(0))
        except ValueError:
            continue


def host_shaped(text: str, allowed=frozenset()) -> list[str]:
    """Host-shaped tokens (two or more letters, then two or more digits) that are not declared nodes.

    Text is split on anything but letters, digits and hyphens into runs. A run, or
    any hyphen-joined slice of it, that equals a declared node is removed. The rest
    is split on hyphens, camelCase and digit-to-letter boundaries, lowercased, and
    each token is matched in full, so case, prefixes and suffixes do not hide one.
    """
    allowed = {name.lower() for name in allowed}
    found = []
    for run in re.split(r"[^A-Za-z0-9-]+", text):
        if UUID.fullmatch(run.lower()):
            continue
        parts = [part for part in run.split("-") if part]
        lowered = [part.lower() for part in parts]
        covered = set()
        for start in range(len(parts)):
            for end in range(start + 1, len(parts) + 1):
                if "-".join(lowered[start:end]) in allowed:
                    covered.update(range(start, end))
        for index, part in enumerate(parts):
            if index in covered or HEX_ID.fullmatch(lowered[index]):
                continue
            spaced = re.sub(r"([a-z])([A-Z])|([0-9])([A-Za-z])", lambda m: " ".join(filter(None, m.groups())), part)
            for token in spaced.lower().split():
                if (HOST_TOKEN.fullmatch(token) and token not in allowed and token not in HOST_SHAPED_ALLOW
                        and not HOST_SHAPED_EXEMPT.fullmatch(token)):
                    found.append(token)
    return list(dict.fromkeys(found))


def text_findings(text: str, *, allow_documentation: bool = False, domains: bool = True) -> list[str]:
    """Addresses, domains and home paths in free text, as problem strings.

    allow_documentation skips loopback, unspecified and documentation addresses
    (RFC 5737, RFC 3849) and throwaway credentials in loopback URLs (framework test
    fixtures); an instance allows none.
    """
    found = []
    for family, raw, address in _addresses(text):
        if allow_documentation and address is not None and (address.is_loopback or address.is_unspecified
                                    or any(address in net for net in DOCUMENTATION_NETS if net.version == address.version)):
            continue
        found.append(f"{family} address {raw}")
    if domains:
        found.extend(f"domain {m.group(0)}" for m in DOTTED.finditer(text) if is_domain(m.group(0)))
    found.extend(f"home or user path {m.group(0)!r}" for m in HOME_PATH.finditer(text))
    found.extend("credential-shaped value (pattern match, not shown)" for _ in CREDENTIAL.finditer(text))
    found.extend("credential in a URL (not shown)" for m in URL_CREDENTIAL.finditer(text)
                 if not (allow_documentation and m.group(1) in LOOPBACK_HOSTS))
    return found


def files(root: Path, skip: set[str]):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(part in skip for part in path.relative_to(root).parts):
            yield path


def denylist_from_env() -> list[str]:
    """SKRSI_ESTATE_DENYLIST: comma or newline separated regexes, a CI secret."""
    raw = os.environ.get("SKRSI_ESTATE_DENYLIST", "")
    return [item.strip() for item in re.split(r"[,\n]", raw) if item.strip()]


def scrub_errors(paths, hostnames: bool = False, denylist=()) -> list[str]:
    """Real-looking addresses, home paths and denylisted names in public trees.

    Framework mode allows documentation and loopback addresses and `~/` (shell
    examples). hostnames=True (the template) allows none of those and also
    rejects domains and host-shaped names. A denylist match is reported by
    pattern index, never by the matched text.
    """
    patterns = [re.compile(item, re.IGNORECASE) for item in denylist if item]
    errors: list[str] = []
    for start in map(Path, paths):
        for path in [start] if start.is_file() else files(start, SCRUB_SKIP_DIRS):
            try:
                text = path.read_bytes().decode("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            problems = text_findings(text, allow_documentation=not hostnames, domains=hostnames)
            if hostnames:
                problems += [f"host-shaped name {name!r}" for name in host_shaped(text)]
            else:
                problems = [p for p in problems if not p.startswith("home or user path '~")]
            problems += [f"denylisted name (pattern {index})" for index, pattern in enumerate(patterns)
                         if pattern.search(text)]
            errors.extend(f"{path.as_posix()}: {problem}" for problem in dict.fromkeys(problems))
    return errors
