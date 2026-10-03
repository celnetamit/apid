"""Sign in with either the username or the email, because both are what people have.

WordPress authenticates on both, and this registry is split almost down the middle:
6,800-odd accounts have an email address as their login (`harshita@stmjournals.com`)
and the rest have a name (`Puneet-Mehrotra`, `amit`). Django's own backend matches the
username only, so half the registry would type the address they have always used and
be told their password is wrong — which is the worst possible failure, because it looks
like the password migration broke.

Email is matched case-insensitively and **only when exactly one account has it**.
Duplicates exist here (the same person registered twice, years apart), and quietly
picking the first would sign somebody into an account that is not the one they meant.
"""

from __future__ import annotations

from django.contrib.auth.backends import ModelBackend

from apps.identity.models import Member


class UsernameOrEmailBackend(ModelBackend):

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username=username, password=password,
                                    **kwargs)
        if user is not None or not username or not password:
            return user

        matches = list(Member.objects.filter(email__iexact=username.strip())[:2])
        if len(matches) != 1:
            # Nothing, or more than one. Either way there is no single right answer,
            # and the timing is kept comparable by hashing anyway.
            Member().set_password(password)
            return None
        candidate = matches[0]
        if candidate.check_password(password) and self.user_can_authenticate(candidate):
            return candidate
        return None
