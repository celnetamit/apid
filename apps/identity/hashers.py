"""Let 13,412 members sign in with the password they already have.

Nobody should have to reset a password because we changed systems. The WordPress hash
comes across untouched and is verified here, and the *first time* a member signs in
their password is re-hashed into Django's own scheme — so the legacy hashes drain away
on their own instead of living for ever.

There are two of them, and assuming one would have locked out a third of the registry:

* **`$P$B…` — phpass**, WordPress's portable hash, on 9,382 accounts. MD5 stretched
  2^N times, where N is a character of the salt.
* **`$wp$2y$…` — bcrypt**, on 4,030 accounts. WordPress 6.8 switched, and it does not
  hash the password directly: it takes the base64 of a SHA-384 HMAC first, keyed with
  the string `wp-sha384`, because bcrypt silently truncates at 72 bytes.

Counted before either was written. Both are verified against real hashes from the
snapshot in `tests/test_hashers.py`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from django.contrib.auth.hashers import BasePasswordHasher
from django.utils.crypto import constant_time_compare

#: phpass's own alphabet. Not base64's, and not a variant of it — the order differs,
#: and using base64 here produces a hash that is wrong without looking wrong.
ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _encode64(source: bytes, count: int) -> str:
    """phpass's base64. Six bits at a time, little-endian, its own alphabet."""
    out = []
    i = 0
    while i < count:
        value = source[i]
        i += 1
        out.append(ITOA64[value & 0x3F])
        if i < count:
            value |= source[i] << 8
        out.append(ITOA64[(value >> 6) & 0x3F])
        if i >= count:
            break
        i += 1
        if i < count:
            value |= source[i] << 16
        out.append(ITOA64[(value >> 12) & 0x3F])
        if i >= count:
            break
        i += 1
        out.append(ITOA64[(value >> 18) & 0x3F])
    return "".join(out)


def phpass_crypt(password: str, setting: str) -> str:
    """The phpass hash of `password` under an existing hash's settings."""
    if len(setting) < 12 or setting[0:3] not in ("$P$", "$H$"):
        return "*"
    count_log2 = ITOA64.find(setting[3])
    if not 7 <= count_log2 <= 30:
        return "*"
    salt = setting[4:12]
    if len(salt) != 8:
        return "*"
    raw = password.encode("utf-8")
    digest = hashlib.md5(salt.encode("ascii") + raw).digest()
    for _ in range(1 << count_log2):
        digest = hashlib.md5(digest + raw).digest()
    return setting[:12] + _encode64(digest, 16)


def wp_bcrypt_password(password: str) -> bytes:
    """What WordPress 6.8+ actually feeds to bcrypt.

    Not the password. bcrypt truncates at 72 bytes, so WordPress pre-hashes with an
    HMAC-SHA384 keyed `wp-sha384` and base64-encodes it. Hash the password directly
    and every one of those 4,030 accounts fails to sign in, with a correct-looking
    implementation.
    """
    digest = hmac.new(b"wp-sha384", password.encode("utf-8"), hashlib.sha384).digest()
    return base64.b64encode(digest)


class WordPressHasher(BasePasswordHasher):
    """Verifies both WordPress formats; never produces one.

    `encode` raises on purpose. This class exists to read what came out of WordPress,
    and a new password must be written in Django's own scheme — otherwise the legacy
    hashes never drain and the day they can be dropped never arrives.
    """

    algorithm = "wordpress"

    def encode(self, password: str, salt: str) -> str:   # pragma: no cover
        raise NotImplementedError(
            "WordPressHasher reads the old hashes; it never writes one. A password "
            "set on this site is hashed by Django's default hasher.")

    def verify(self, password: str, encoded: str) -> bool:
        if not encoded:
            return False
        if encoded.startswith("$wp$2y$"):
            try:
                import bcrypt
            except ImportError:                          # pragma: no cover
                return False
            # `encoded[3:]`, not `[4:]`: the stored string is `$wp$2y$10$…` and the
            # bcrypt hash is `$2y$10$…`, so the `$` before `2y` is shared. Slicing one
            # character further hands bcrypt `2y$10$…`, which it rejects — and the
            # failure looks exactly like a wrong password.
            stripped = encoded[3:]
            try:
                return bcrypt.checkpw(wp_bcrypt_password(password),
                                      stripped.encode("ascii"))
            except ValueError:
                return False
        if encoded.startswith(("$P$", "$H$")):
            return constant_time_compare(phpass_crypt(password, encoded), encoded)
        # Very old WordPress stored bare MD5. None are in this snapshot, and it is
        # handled rather than crashing on a member imported later.
        if len(encoded) == 32 and all(c in "0123456789abcdef" for c in encoded.lower()):
            return constant_time_compare(
                hashlib.md5(password.encode("utf-8")).hexdigest(), encoded.lower())
        return False

    def safe_summary(self, encoded: str) -> dict:
        kind = ("bcrypt" if encoded.startswith("$wp$") else
                "phpass" if encoded.startswith(("$P$", "$H$")) else "unknown")
        return {"algorithm": self.algorithm, "format": kind,
                "hash": encoded[:12] + "…"}

    def must_update(self, encoded: str) -> bool:
        # Always: a member who signs in successfully gets their password re-hashed
        # into Django's scheme, and the legacy hash goes.
        return True

    def harden_runtime(self, password: str, encoded: str) -> None:
        pass
