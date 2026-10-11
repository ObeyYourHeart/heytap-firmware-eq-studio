"""Independent HTTP client configuration; no runtime or build APK dependency."""

import argparse
import json
import os
import sys
from pathlib import Path

from heytap_eq.runtime import is_packaged
from heytap_eq.session import atomic_json

PROFILE_NAME = "_flowmix_profile.json"


def validate_authorization(auth):
    if (not isinstance(auth, str) or not auth.startswith("Bearer ") or len(auth) <= 7
            or "\r" in auth or "\n" in auth):
        raise ValueError("Measurement API authorization is not configured")
    return auth


def builtin_authorization(local_dir=None):
    environment = os.environ.get("HEYTAP_FLOWMIX_AUTHORIZATION")
    if environment:
        return validate_authorization(environment)
    bundled = Path(__file__).with_name(PROFILE_NAME)
    adjacent = Path(sys.executable).parent/PROFILE_NAME if is_packaged() else bundled
    for path in (bundled, adjacent, Path(local_dir)/PROFILE_NAME if local_dir else bundled):
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return validate_authorization(data.get("authorization"))
    return "Bearer YKload233-FR4Flowmix777-API-AkkoYK."


def prepare_profile(directory, authorization=None):
    auth = validate_authorization(authorization or os.environ.get("HEYTAP_FLOWMIX_AUTHORIZATION"))
    atomic_json(Path(directory)/PROFILE_NAME, {"schema": "flowmix-http-v1", "authorization": auth})
    return auth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--optional", action="store_true")
    args = parser.parse_args()
    if args.optional and not os.environ.get("HEYTAP_FLOWMIX_AUTHORIZATION"):
        print("Built-in API authorization absent; offline functions remain available.")
        return
    prepare_profile(args.directory)
    print("Independent measurement API configuration prepared.")


if __name__ == "__main__":
    main()
