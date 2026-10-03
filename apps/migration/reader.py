"""Reading Formidable Forms without believing anything it does not say.

Formidable keeps a submission as one row in `frm_items` and one row per answered
field in `frm_item_metas`. A field that was left empty has **no row at all**, so the
absence of a value and the value "empty" are the same thing here and neither can be
told from a field that was added to the form last week.

Two rules come out of that, and both are load-bearing:

* **Join on `field_key`, never on `field_id`.** Ids are per-field and survive edits,
  but a form that is duplicated — and this site has several clones of its own
  registration form — gets new ids for the same logical field. The key is what the
  site's own views use.
* **A missing key is reported, not defaulted.** `get()` returning `""` for a field
  nobody has ever filled in and for a field whose key we spelled wrong is how an
  import loses a column in silence.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import pymysql


#: The site runs on IST (`gmt_offset: 5.5` in its own REST index) and stores naive
#: datetimes. Reading them as UTC would move every registration date back by five and
#: a half hours — enough to put a late-evening sign-up on the previous day.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def aware(value):
    """A stored datetime, with the timezone the site actually keeps."""
    if value is None or getattr(value, "tzinfo", None) is not None:
        return value
    return value.replace(tzinfo=IST)


@dataclass
class Entry:
    """One submission: its own row, plus the values that exist for it."""

    id: int
    form_id: int
    user_id: Optional[int]
    created_at: Optional[dt.datetime]
    updated_at: Optional[dt.datetime]
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def has(self, key: str) -> bool:
        return key in self.values


#: Formidable stores anything repeatable as a PHP serialised array, and a single
#: value as itself. `a:1:{i:0;s:5:"India";}` is one country, not a string.
_SERIALISED = re.compile(r'^a:\d+:\{')
_PHP_STRING = re.compile(r's:\d+:"(.*?)";', re.S)


#: Every item in a serialised array body, key and value alike, in order. A PHP array
#: alternates them, so the values are the odd positions — which is the whole reason
#: this exists instead of matching strings alone.
_PHP_ITEM = re.compile(r's:\d+:"(.*?)";|i:(-?\d+);|d:([^;]+);|b:([01]);|(N;)', re.S)


def unserialise(value: str) -> list[str]:
    """The **values** inside a PHP array, or the single value as a one-item list.

    The subtlety that cost a wrong name on every older member: a Formidable name field
    is an *associative* array —
    `a:2:{s:5:"first";s:7:"Tanisha";s:4:"last";s:6:"Sirohi";}` — and matching every
    quoted string returns the keys as well, so the member came out as
    `first Tanisha last Sirohi`. An indexed array (`a:1:{i:0;s:5:"India";}`) hides the
    problem completely, because its keys are integers and never match a string pattern.

    So the body is walked as alternating key/value and only the values are taken.
    Deliberately still not `phpserialize`: nothing here is evaluated, and the only
    shapes on this site are flat arrays and scalars.
    """
    if not value:
        return []
    if not _SERIALISED.match(value):
        return [html.unescape(value)]
    items = []
    for match in _PHP_ITEM.finditer(value):
        items.append(next(g for g in match.groups() if g is not None))
    # key, value, key, value …
    return [html.unescape(v) for v in items[1::2]]


def first(value: str) -> str:
    """The single value a field holds, whichever way it is stored.

    HTML-unescaped, because WordPress stores what the browser posted: an affiliation
    called `Aditi &amp; Anjali` is not called that.
    """
    items = unserialise(value)
    return items[0] if items else ""


def joined(value: str, separator: str = " ") -> str:
    """Every value a field holds, in order — a name's parts, a multi-select's picks."""
    return separator.join(v for v in unserialise(value) if v and v.strip())


class WordPress:
    """A read-only session against the snapshot.

    Read-only is not a convention here, it is the point: this class issues `SELECT`
    and nothing else, so no mistake in anything above it can reach the live site or
    even the snapshot.
    """

    def __init__(self, host: str = None, port: int = None,
                 user: str = None, password: str = None,
                 database: str = None, prefix: str = None) -> None:
        import os as _os
        host = host or _os.environ.get('WP_DB_HOST', '127.0.0.1')
        port = port or int(_os.environ.get('WP_DB_PORT', '3399'))
        user = user or _os.environ.get('WP_DB_USER', 'root')
        password = password or _os.environ.get('WP_DB_PASSWORD', '')
        database = database or _os.environ.get('WP_DB_NAME', 'apid')
        prefix = prefix or _os.environ.get('WP_DB_PREFIX', 'wpapid_')
        self.prefix = prefix
        self._db = pymysql.connect(
            host=host, port=port, user=user, password=password, database=database,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)

    def close(self) -> None:
        self._db.close()

    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._db.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    # --- forms ---------------------------------------------------------------

    def forms(self) -> list[dict[str, Any]]:
        return self._rows(
            f"SELECT id, form_key, name, created_at FROM {self.prefix}frm_forms "
            f"ORDER BY id")

    def fields(self, form_id: int) -> dict[str, dict[str, Any]]:
        """`{field_key: field}` for one form."""
        rows = self._rows(
            f"SELECT id, field_key, name, type, required, field_order "
            f"FROM {self.prefix}frm_fields WHERE form_id=%s ORDER BY field_order",
            (form_id,))
        return {r["field_key"]: r for r in rows}

    def entry_count(self, form_id: int) -> int:
        return self._rows(
            f"SELECT COUNT(*) AS n FROM {self.prefix}frm_items WHERE form_id=%s",
            (form_id,))[0]["n"]

    def entries(self, form_id: int, batch: int = 2000) -> Iterator[Entry]:
        """Every submission of one form, in id order, with its values attached.

        Batched because the meta table has 539,542 rows: loading one form's values in
        one query is fine, loading every form's is not.
        """
        keys = {f["id"]: key for key, f in self.fields(form_id).items()}
        items = self._rows(
            f"SELECT id, form_id, user_id, created_at, updated_at "
            f"FROM {self.prefix}frm_items WHERE form_id=%s ORDER BY id", (form_id,))

        for start in range(0, len(items), batch):
            chunk = items[start:start + batch]
            ids = [row["id"] for row in chunk]
            placeholders = ",".join(["%s"] * len(ids))
            metas = self._rows(
                f"SELECT item_id, field_id, meta_value FROM {self.prefix}frm_item_metas "
                f"WHERE item_id IN ({placeholders})", tuple(ids))
            by_item: dict[int, dict[str, str]] = {i: {} for i in ids}
            for meta in metas:
                key = keys.get(meta["field_id"])
                # A meta row whose field has been deleted from the form still exists.
                # It is not ours to interpret, and silently keeping it under its id
                # would put a number where a key belongs.
                if key is not None:
                    by_item[meta["item_id"]][key] = meta["meta_value"] or ""
            for row in chunk:
                yield Entry(id=row["id"], form_id=row["form_id"],
                            user_id=row["user_id"] or None,
                            created_at=aware(row["created_at"]),
                            updated_at=aware(row["updated_at"]),
                            values=by_item[row["id"]])

    # --- users ---------------------------------------------------------------

    def users(self) -> list[dict[str, Any]]:
        rows = self._rows(
            f"SELECT ID, user_login, user_email, user_pass, user_registered, "
            f"display_name FROM {self.prefix}users ORDER BY ID")
        for row in rows:
            row["user_registered"] = aware(row["user_registered"])
            # The users table is stored HTML-encoded too. Three members are called
            # something with an `&` in it, and without this they stay `&amp;` — which
            # the form fields would have escaped but a display-name fallback would not.
            row["display_name"] = html.unescape(row.get("display_name") or "")
        return rows

    def user_meta(self, keys: tuple[str, ...]) -> dict[int, dict[str, str]]:
        placeholders = ",".join(["%s"] * len(keys))
        rows = self._rows(
            f"SELECT user_id, meta_key, meta_value FROM {self.prefix}usermeta "
            f"WHERE meta_key IN ({placeholders})", keys)
        out: dict[int, dict[str, str]] = {}
        for row in rows:
            out.setdefault(row["user_id"], {})[row["meta_key"]] = row["meta_value"]
        return out


#: The WordPress capability names, mapped to ours. Anything not here is reported by
#: the import rather than dropped — a role nobody has heard of is worth a look.
CAPABILITY_ROLES = {
    "author": "author",
    "editor": "editor",
    "editor-in-chief": "editor_in_chief",
    "comissioning-editor": "commissioning_editor",   # sic, as stored
    "commissioning-editor": "commissioning_editor",
    "reviewer": "reviewer",
    "subscriber": "subscriber",
    "techsupport": "tech_support",
    "administrator": "administrator",
    "contributor": "author",
}


def roles_from_capabilities(serialised: str) -> tuple[list[str], list[str]]:
    """`(known roles, unrecognised capability names)`.

    WordPress stores capabilities as `a:2:{s:13:"administrator";b:1;…}`. Only the
    string keys matter, and only the ones that name a role — `frm_view_forms` is a
    permission, not a role, and it appears in the same array.
    """
    names = [m.group(1) for m in _PHP_STRING.finditer(serialised or "")]
    known, unknown = [], []
    for name in names:
        if name in CAPABILITY_ROLES:
            known.append(CAPABILITY_ROLES[name])
        elif name and not name.startswith("frm_") and name != "pending":
            unknown.append(name)
    return list(dict.fromkeys(known)), list(dict.fromkeys(unknown))
