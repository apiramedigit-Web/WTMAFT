"""Backend keyword fine-tuning (requirement section 4/5 "Keyword Optimization Principle").

Existing keywords are KEPT; only these are removed:
  * duplicate words   - a word already present earlier in the field (case-insensitive,
                        surrounding punctuation ignored for comparison only)
  * repeated terms    - a whole keyword entry identical to an earlier entry
  * whitespace noise  - leading/trailing/double spaces, empty entries
Nothing is added, re-ordered, translated or rewritten. The first occurrence of every
word keeps its original spelling and position. No "unnecessary word" list (stop words,
brand terms, byte-limit trimming) is applied: no documented project rule exists for it.
"""
import re

_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def _norm(word):
    return _EDGE_PUNCT.sub("", word).casefold()


def finetune(entries):
    """entries: backend keyword values in view_order. Returns an analysis dict."""
    original = [e for e in entries if e is not None]
    seen_entries, repeated_entries, kept_entries = set(), [], []
    for e in original:
        key = " ".join(e.split()).casefold()
        if not key:
            continue
        if key in seen_entries:
            repeated_entries.append(" ".join(e.split()))
            continue
        seen_entries.add(key)
        kept_entries.append(e)

    seen, kept, removed = set(), [], []
    for e in kept_entries:
        for w in e.split():
            n = _norm(w)
            if not n:  # pure punctuation token: keep as-is, not a keyword
                kept.append(w)
                continue
            if n in seen:
                removed.append(w)
            else:
                seen.add(n)
                kept.append(w)

    original_text = " ".join(" ".join(e.split()) for e in original if e.strip())
    cleaned_text = " ".join(kept)
    whitespace_only = (not removed and not repeated_entries
                       and original_text != " ".join(original))

    if repeated_entries and removed:
        issue, action = "Repeated keywords + duplicate words", "Removed repeated terms and duplicates"
    elif repeated_entries:
        issue, action = "Repeated keywords", "Removed repeated terms"
    elif removed:
        issue, action = "Duplicate words", "Removed duplicates"
    else:
        issue, action = "No duplicate or repeated terms", "No change required"

    orig_words = len(original_text.split())
    return {
        "original": original_text,
        "cleaned": cleaned_text,
        "removed_words": removed,
        "repeated_entries": repeated_entries,
        "n_entries": len(original),
        "original_word_count": orig_words,
        "cleaned_word_count": len(kept),
        "duplicate_words_removed": len(removed),
        "original_bytes": len(original_text.encode("utf-8")),
        "cleaned_bytes": len(cleaned_text.encode("utf-8")),
        "issue": issue,
        "action": action,
        "needs_change": bool(removed or repeated_entries),
        "whitespace_only": whitespace_only,
    }


if __name__ == "__main__":
    r = finetune(["pendant light Pendant  light, kitchen", " kitchen lamp", " kitchen lamp"])
    assert r["cleaned"] == "pendant light kitchen lamp" or r["cleaned"].startswith("pendant light"), r
    assert r["repeated_entries"] == ["kitchen lamp"], r
    assert "Pendant" in r["removed_words"] and "light," in r["removed_words"], r
    # every unique word survives
    assert {w.casefold().strip(",") for w in r["cleaned"].split()} == {"pendant", "light", "kitchen", "lamp"}
    print("self-test OK", r["cleaned"], r["removed_words"])
