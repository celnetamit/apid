"""Who to follow, and where to send your paper — from what the registry knows.

Amit, 17 Sep 2026: *"isme smart recommendation system bhi bana sakte hai kya, like
Netflix and Amazon?"*

**Netflix's method cannot work here, and the measurement is not close.** Collaborative
filtering — *people who watched this also watched that* — needs co-occurring behaviour.
This registry has 13,412 members and:

* 176 follow edges and 168 likes in total;
* **26 members** who follow two or more people — the minimum for a co-occurrence to
  exist at all;
* **3 pairs** of people followed together more than once.

Netflix runs on billions of ratings. Three co-occurrences produce nothing, and a system
built on them would recommend the same four people to all 13,412 members.

**But this registry has the thing Netflix does not have: it can read its own items.**
Netflix needs collaborative filtering *because* it cannot read a film. Here there are
4,170 profiles carrying an expertise, 4,194 an affiliation, and 6,409 publication
titles across 1,195 members. Content is the right signal, not the consolation prize.

So: TF-IDF over each member's own words, cosine similarity between members, and the
same vector matched against the 278 journals' subjects and scope.

**Rare words carry the meaning.** `research`, `university` and `study` appear in
thousands of profiles and separate nobody; `tribology`, `vitrimer` and `bioremediation`
appear in a handful and separate everybody. That is exactly what the inverse document
frequency computes, which is why it is used rather than a keyword overlap — the naive
version recommends the whole of engineering to every engineer.

**Neighbours are computed once and stored.** Similarity over 13,412 members is 90
million pairs; doing it per page view is the query that works in testing and falls over
on the day somebody links to the site. `rebuild_recommendations` writes the top twenty
per member, and a page read is one indexed lookup.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

#: Words that appear everywhere in an academic registry and mean nothing in it. Every
#: one of these was in the top 40 by document frequency over the real profiles.
STOPWORDS = frozenset("""
    a an the and or of in on at to for with by from as is are was were be been being
    this that these those it its their his her our your my we they i you he she
    research researcher study studies work working experience field area areas
    university college institute institution department faculty school professor
    assistant associate lecturer scholar student phd msc bsc ltd pvt india indian
    science sciences engineering technology technologies applied general national
    international journal journals paper papers published publication publications
    also more than have has had can may such using used use based various new
    including include included well many most other others over under between
    """.split()) | frozenset("""
    celnet stmjournals stm journals nanoschool celnetamit consortium elearning
    test testing demo sample dummy example
    """.split())
#: The second group is this house's own names and the words a test profile is made of.
#: `celnet` appeared as the *reason* two web developers were matched — it is where they
#: work, not what they do, and a recommendation has to say something the person could
#: not have worked out themselves.

_WORD = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")


def tokens(text: str) -> List[str]:
    """The words worth keeping: three letters or more, lowercase, no boilerplate."""
    return [w for w in (m.group(0).lower() for m in _WORD.finditer(text or ""))
            if w not in STOPWORDS and len(w) > 2]


def build_idf(documents: Iterable[List[str]]) -> Dict[str, float]:
    """`word -> inverse document frequency` over the whole corpus."""
    seen_in = Counter()
    total = 0
    for doc in documents:
        total += 1
        for word in set(doc):
            seen_in[word] += 1
    if not total:
        return {}
    # Smoothed, so a word in every document scores near zero rather than exactly zero,
    # and a word in one document does not dominate by an unbounded amount.
    return {word: math.log((1 + total) / (1 + n)) + 1.0
            for word, n in seen_in.items()}


def vector(doc: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    """A unit-length TF-IDF vector. Normalised, so a long biography does not beat a
    short one on length alone — which it did, and put the same three verbose profiles
    at the top of everybody's list."""
    if not doc:
        return {}
    counts = Counter(doc)
    most = max(counts.values())
    raw = {word: (0.5 + 0.5 * n / most) * idf.get(word, 1.0)
           for word, n in counts.items()}
    length = math.sqrt(sum(v * v for v in raw.values())) or 1.0
    return {word: v / length for word, v in raw.items()}


def cosine(a: Dict[str, float], b: Dict[str, float]) -> float:
    """Both vectors are unit length, so the dot product is the cosine."""
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(word, 0.0) for word, weight in a.items())


def nearest(target: Dict[str, float], others: Dict[int, Dict[str, float]],
            index: Dict[str, List[int]], limit: int = 20,
            skip: Iterable[int] = ()) -> List[Tuple[int, float]]:
    """The closest `limit` of `others`, found through an inverted index.

    Comparing against all 13,412 would be 13,412 dot products per member and 90 million
    for a rebuild. Only the candidates that share a word can score above zero, so the
    index is what makes this finish: a rare word names a handful of people and a common
    one has already been weighted down to nothing.
    """
    skip = set(skip)
    scores: Dict[int, float] = defaultdict(float)
    # The rarest words first — they are the ones that actually identify anybody, and
    # they keep the candidate set small.
    for word, weight in sorted(target.items(), key=lambda kv: -kv[1])[:40]:
        for other_id in index.get(word, ()):
            if other_id in skip:
                continue
            scores[other_id] += weight * others[other_id].get(word, 0.0)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [(i, round(s, 4)) for i, s in ranked[:limit] if s > 0.01]


def invert(vectors: Dict[int, Dict[str, float]]) -> Dict[str, List[int]]:
    """`word -> the ids whose vector contains it`."""
    index: Dict[str, List[int]] = defaultdict(list)
    for owner, vec in vectors.items():
        for word in vec:
            index[word].append(owner)
    return index
