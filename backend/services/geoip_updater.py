"""Download and update the MaxMind GeoLite2-City database."""
import logging
import os
import tarfile
import tempfile

import httpx

log = logging.getLogger("nodeglow.geoip_updater")

GEOIP_DIR = "/data/geoip"
GEOIP_DB_FILE = "GeoLite2-City.mmdb"
GEOIP_DB_PATH = os.path.join(GEOIP_DIR, GEOIP_DB_FILE)

DOWNLOAD_URL = (
    "https://download.maxmind.com/app/geoip_download"
    "?edition_id=GeoLite2-City&license_key={key}&suffix=tar.gz"
)


async def download_geolite2(license_key: str) -> dict:
    """Download GeoLite2-City.mmdb from MaxMind and extract to /data/geoip/.

    Returns {"success": bool, "message": str, "size_mb": float|None}.
    """
    if not license_key:
        return {"success": False, "message": "No license key provided", "size_mb": None}

    url = DOWNLOAD_URL.format(key=license_key)
    log.info("Downloading GeoLite2-City database from MaxMind...")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(url, follow_redirects=True)

            if resp.status_code == 401:
                return {"success": False, "message": "Invalid license key (401 Unauthorized)", "size_mb": None}
            if resp.status_code != 200:
                return {
                    "success": False,
                    "message": f"Download failed: HTTP {resp.status_code}",
                    "size_mb": None,
                }

            # Write tarball to a temp file, then extract .mmdb
            os.makedirs(GEOIP_DIR, exist_ok=True)

            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

        try:
            mmdb_found = False
            with tarfile.open(tmp_path, "r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith(GEOIP_DB_FILE):
                        # Extract the .mmdb file directly to target path
                        source = tar.extractfile(member)
                        if source is None:
                            continue
                        with open(GEOIP_DB_PATH, "wb") as dest:
                            dest.write(source.read())
                        mmdb_found = True
                        break

            if not mmdb_found:
                return {
                    "success": False,
                    "message": "Archive did not contain GeoLite2-City.mmdb",
                    "size_mb": None,
                }

            size_mb = round(os.path.getsize(GEOIP_DB_PATH) / (1024 * 1024), 1)
            log.info("GeoLite2-City.mmdb updated successfully (%.1f MB)", size_mb)

            # Reset the cached reader so it picks up the new file
            import services.geoip as _geoip_mod
            _geoip_mod._db = None
            _geoip_mod._db_loaded = False
            _geoip_mod.resolve.cache_clear()

            return {"success": True, "message": "Database updated successfully", "size_mb": size_mb}

        finally:
            os.unlink(tmp_path)

    except httpx.TimeoutException:
        log.error("GeoIP download timed out")
        return {"success": False, "message": "Download timed out", "size_mb": None}
    except Exception as exc:
        log.error("GeoIP download failed: %s", exc)
        return {"success": False, "message": f"Download failed: {exc}", "size_mb": None}
