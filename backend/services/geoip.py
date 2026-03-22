"""GeoIP enrichment — resolve IPs from syslog messages to country/city.

Uses MaxMind GeoLite2 database (mmdb format). The database file is loaded
on first use from /data/geoip/GeoLite2-City.mmdb or a path configured via
the GEOIP_DB_PATH environment variable.

If no database is available, enrichment is silently skipped.
"""
import ipaddress
import logging
import os
import re
from functools import lru_cache
from typing import Optional

log = logging.getLogger("nodeglow.geoip")

_db = None
_db_loaded = False
_IP_RE = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b')

# Paths to try for GeoLite2 database
_DB_PATHS = [
    os.environ.get("GEOIP_DB_PATH", ""),
    "/data/geoip/GeoLite2-City.mmdb",
    "/app/data/geoip/GeoLite2-City.mmdb",
    "data/geoip/GeoLite2-City.mmdb",
]


def _load_db():
    """Lazily load the MaxMind database."""
    global _db, _db_loaded
    if _db_loaded:
        return _db
    _db_loaded = True

    try:
        import maxminddb
    except ImportError:
        log.info("GeoIP disabled: maxminddb not installed (pip install maxminddb)")
        return None

    for path in _DB_PATHS:
        if path and os.path.isfile(path):
            try:
                _db = maxminddb.open_database(path, maxminddb.MODE_MMAP)
                log.info("GeoIP database loaded: %s", path)
                return _db
            except Exception as e:
                log.warning("Failed to load GeoIP database %s: %s", path, e)

    log.info("GeoIP disabled: no GeoLite2-City.mmdb found")
    return None


def _is_private_ip(ip_str: str) -> bool:
    """Check if IP is RFC1918/link-local/loopback."""
    try:
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return True


@lru_cache(maxsize=10000)
def resolve(ip: str) -> Optional[dict]:
    """Resolve an IP to {country, city, lat, lon}. Returns None on failure."""
    if _is_private_ip(ip):
        return None

    db = _load_db()
    if db is None:
        return None

    try:
        result = db.get(ip)
        if not result:
            return None
        country = ""
        city = ""
        if "country" in result and "iso_code" in result["country"]:
            country = result["country"]["iso_code"]
        if "city" in result and "names" in result["city"]:
            city = result["city"]["names"].get("en", "")
        if not country:
            return None
        return {"country": country, "city": city}
    except Exception:
        return None


def enrich_message(message: str) -> dict:
    """Extract first external IP from a message and resolve it.
    Returns {geo_country, geo_city} or empty strings."""
    result = {"geo_country": "", "geo_city": ""}
    if not message:
        return result

    for match in _IP_RE.finditer(message):
        ip = match.group(1)
        if _is_private_ip(ip):
            continue
        geo = resolve(ip)
        if geo:
            result["geo_country"] = geo["country"]
            result["geo_city"] = geo["city"]
            break

    return result
