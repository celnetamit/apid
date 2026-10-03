"""Share widget endpoints: QR code and profile card images.

Both are generated on request (not cached to disk) because:
  - a profile picture / name / affiliation can change at any time, and a stale
    card is worse than a fresh one;
  - the images are small (QR < 5 KB, card < 60 KB) so re-rendering is cheap;
  - caching at the HTTP layer (Caddy / Cloudflare) handles the real-world load.

A `?ref=<APID>` query param on registration links lets the referrer be tracked;
we capture it in `capture_referral()` and apply it in `apps.identity.views.register`.
"""
from __future__ import annotations

import io
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, HttpResponse, HttpResponseRedirect, Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.cache import cache_control

from apps.identity.models import Member


def _public_url(request, path: str) -> str:
    """Absolute URL (scheme + host + path) that others can open."""
    return request.build_absolute_uri(path)


def profile_qr(request, apid: str):
    """QR code pointing to the public profile page. 400 × 400 PNG."""
    import qrcode  # local import — the dep is only here
    member = get_object_or_404(Member, apid=apid)
    url = _public_url(request, f"/profiles/{member.apid}/")
    img = qrcode.make(url, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    # Allow Cloudflare / browsers to cache for a day — the URL does not change.
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


def profile_card(request, apid: str):
    """A nice-looking card image people can save and share.

    1200 × 630 — the Open Graph image size preferred by LinkedIn and Twitter so
    one asset covers both the public card download AND the og:image preview.
    """
    from PIL import Image, ImageDraw, ImageFont
    member = get_object_or_404(Member.objects.select_related("profile"), apid=apid)
    profile = getattr(member, "profile", None)

    W, H = 1200, 630
    bg = Image.new("RGB", (W, H), (15, 27, 45))     # --ink
    draw = ImageDraw.Draw(bg)

    # Right-side gradient band in brand blue (approximation — paint 6 vertical bands).
    for i, alpha in enumerate((40, 55, 75, 100, 130, 160)):
        x0 = W - (6 - i) * 25
        draw.rectangle([x0, 0, x0 + 25, H], fill=(30, 136, 229, alpha))
    draw.rectangle([W - 10, 0, W, H], fill=(245, 166, 35))  # orange accent stripe

    # Fonts — fall back to default if DejaVu is missing.
    def _font(name: str, size: int):
        candidates = [
            f"/usr/share/fonts/truetype/dejavu/{name}",
            f"/usr/share/fonts/dejavu/{name}",
        ]
        for c in candidates:
            if Path(c).exists():
                try:
                    return ImageFont.truetype(c, size)
                except Exception:                                 # noqa: BLE001
                    pass
        return ImageFont.load_default()

    f_eyebrow = _font("DejaVuSans-Bold.ttf", 20)
    f_name = _font("DejaVuSans-Bold.ttf", 56)
    f_sub = _font("DejaVuSans.ttf", 26)
    f_affil = _font("DejaVuSans.ttf", 22)
    f_apid = _font("DejaVuSans-Bold.ttf", 32)
    f_url = _font("DejaVuSansMono.ttf", 20)

    # Avatar — profile photo or coloured initial.
    AX, AY, AS = 70, 70, 180
    if profile and profile.picture:
        try:
            pic_path = Path(settings.MEDIA_ROOT) / str(profile.picture)
            with Image.open(pic_path) as pic:
                pic = pic.convert("RGB")
                pic.thumbnail((AS, AS), Image.LANCZOS)
                # Centre-crop to a square.
                pw, ph = pic.size
                side = min(pw, ph)
                pic = pic.crop(((pw - side) // 2, (ph - side) // 2,
                                (pw + side) // 2, (ph + side) // 2))
                pic = pic.resize((AS, AS), Image.LANCZOS)
                # Rounded mask.
                mask = Image.new("L", (AS, AS), 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, AS, AS), radius=20, fill=255)
                bg.paste(pic, (AX, AY), mask)
        except Exception:                                         # noqa: BLE001
            # Fall through to initial.
            profile_picture_rendered = False
        else:
            profile_picture_rendered = True
    else:
        profile_picture_rendered = False

    if not profile_picture_rendered:
        draw.rounded_rectangle([AX, AY, AX + AS, AY + AS], radius=20,
                               fill=(30, 136, 229))
        initial = (member.display_name or member.username or "A")[:1].upper()
        bbox = draw.textbbox((0, 0), initial, font=_font("DejaVuSans-Bold.ttf", 110))
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((AX + (AS - tw) // 2 - bbox[0], AY + (AS - th) // 2 - bbox[1]),
                  initial, font=_font("DejaVuSans-Bold.ttf", 110), fill=(255, 255, 255))

    # Right of avatar: name, designation, affiliation.
    TX = AX + AS + 36
    draw.text((TX, AY + 10), "APID VERIFIED SCHOLAR",
              font=f_eyebrow, fill=(245, 166, 35))
    name = member.display_name or member.username or ""
    name = name[:48] + ("…" if len(name) > 48 else "")
    draw.text((TX, AY + 40), name, font=f_name, fill=(255, 255, 255))

    sub_y = AY + 110
    if profile and profile.designation:
        txt = profile.designation
        if profile.department:
            txt += f" · {profile.department}"
        txt = txt[:62] + ("…" if len(txt) > 62 else "")
        draw.text((TX, sub_y), txt, font=f_sub, fill=(203, 213, 225))
        sub_y += 36
    if profile and profile.affiliation:
        aff = profile.affiliation[:68] + ("…" if len(profile.affiliation) > 68 else "")
        draw.text((TX, sub_y), aff, font=f_affil, fill=(148, 163, 184))

    # Bottom strip — APID and resolve URL.
    draw.rectangle([0, H - 110, W, H], fill=(16, 32, 44))
    draw.text((70, H - 90), f"APID {member.apid}", font=f_apid, fill=(245, 166, 35))
    resolve = f"apid.celnet.in/profiles/{member.apid}/"
    draw.text((70, H - 48), resolve, font=f_url, fill=(203, 213, 225))

    # QR in the bottom-right corner.
    try:
        import qrcode
        qimg = qrcode.make(
            _public_url(request, f"/profiles/{member.apid}/"),
            box_size=4, border=1,
        ).convert("RGB")
        qimg = qimg.resize((110, 110), Image.LANCZOS)
        bg.paste(qimg, (W - 150, H - 150))
    except Exception:                                             # noqa: BLE001
        pass

    buf = io.BytesIO()
    bg.save(buf, format="PNG")
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type="image/png")
    resp["Cache-Control"] = "public, max-age=3600"
    return resp


def capture_referral(request, apid: str):
    """`/r/<apid>/` → stash referrer in the session and 302 to registration.

    A plain-English URL for printed material and QR codes: `apid.celnet.in/r/APID1234`
    lands at /registration/ with the referrer already captured.
    """
    if Member.objects.filter(apid=apid).exists():
        request.session["referral_apid"] = apid
    # Registration lives at /accounts/register/ (/registration/ 301s there).
    return HttpResponseRedirect("/accounts/register/")
