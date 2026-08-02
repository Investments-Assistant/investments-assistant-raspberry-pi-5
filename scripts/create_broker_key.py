#!/usr/bin/env python3
"""Generate the Fernet key used for encrypted per-user broker credentials."""

from cryptography.fernet import Fernet

if __name__ == "__main__":
    print("BROKER_CREDENTIALS_KEY=" + Fernet.generate_key().decode("ascii"))
