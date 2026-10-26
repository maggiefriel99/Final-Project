"""Checks on untrusted text (constituent requests) before and after it reaches an agent."""

import re
import unicodedata

MAX_REQUEST_CHARS = 3000

# Phrases that try to instruct the system rather than describe a problem. Matching one does not
# block the request; it marks it for human review and is shown to the reviewer.
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any |the )?(previous|prior|above|your) (instructions|rules)",
    r"\b(system|developer) (prompt|message|mode)\b",
    r"\byou are now\b",
    r"\b(act|behave|respond) as (an? |the )?(admin|administrator|system|developer|root|unrestricted|different (ai|assistant|model))\b",
    r"\bnew (system )?(instructions?|rules?) ?(for|to) (you|the (ai|assistant|system|model|agent))\b",
    r"\b(administrator|admin|developer|god|debug) mode\b",
    r"\boverride (your|the|all|any|previous|system|safety) (instructions|rules|settings|prompt|filters?)\b",
    r"\b(mark|classify|route|flag|treat|categori[sz]e)\b[^.\n]{0,40}\bas (capital|operating|approved|funded)\b",
    r"\bpre-?approved\b",
    r"\bnote to (the )?(ai|system|assistant|triage|model|agent)\b",
    r"\b(approve|authori[sz]e) (this|the|a) (payment|budget|project|funding)\b",
    r"\b(api[_ ]?keys?|access tokens?|auth(entication)? tokens?|passwords?|secret keys?)\b",
    r"\breveal\b.*\b(prompt|instructions|rules)\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
    # The same attacks in other languages NYC residents use (Spanish, French, Chinese, Russian, Korean).
    r"\b(ignora|olvida|omite|ignorez|ignore|oubliez?)\b[^.\n]{0,30}\b(instrucciones|reglas|indicaciones|instructions|règles|consignes)\b",
    r"\b(marca|clasifica|trata)\b[^.\n]{0,40}\bcomo (capital|aprobad[oa]|financiad[oa])\b",
    r"(忽略|无视|忽视|忘记|忘掉)[^。\n]{0,12}(指令|指示|说明|规则|提示)",
    r"(系统提示|开发者模式|管理员模式)",
    r"(игнорир\w*|забудь\w*)[^.\n]{0,30}(инструкци\w*|правил\w*)",
    r"(지시|명령|규칙|지침)[^.\n]{0,12}(무시|잊어)|(무시|잊어)[^.\n]{0,12}(지시|명령|규칙|지침)",
]


def clean_request(text: str) -> tuple[str, list[str]]:
    """Normalize and cap untrusted text. Returns the cleaned text and notes for the reviewer."""
    notes = []
    text = unicodedata.normalize("NFKC", text or "")
    # Drop control and invisible formatting characters that can hide instructions.
    cleaned = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    if cleaned != text:
        notes.append("Removed hidden or control characters.")
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    if len(cleaned) > MAX_REQUEST_CHARS:
        cleaned = cleaned[:MAX_REQUEST_CHARS]
        notes.append(f"Request was cut to {MAX_REQUEST_CHARS:,} characters.")
    return cleaned, notes


def injection_flags(text: str) -> list[str]:
    """Phrases in the text that look like instructions to the system."""
    found = []
    for pattern in INJECTION_PATTERNS:
        m = re.search(pattern, text, flags=re.I)
        if m:
            found.append(m.group(0))
    return found


# ---------- Checks on what the agent wrote, before another agent or a person acts on it ----------

MAX_SUMMARY_CHARS = 300
# Street names: letters, digits, and ordinary punctuation only. No ; = : < > { } or quotes that could
# carry instructions or markup. (Case and "and" are fine: "80 STREET and 81 STREET".)
STREET_FORMAT = r"[A-Za-z0-9][A-Za-z0-9 '\-./&,]{0,59}"
OUTPUT_EXTRA_PATTERNS = [
    r"https?://|www\.",                         # links an attacker wants forwarded
    r"[\w.+-]+@[\w-]+\.[\w.]+",                  # email addresses
    r"\$\s?\d[\d,]*(?:\.\d+)?\s*(?:k|m|million|thousand)?\b",  # dollar amounts (code supplies all money figures)
]
FUNDING_CLAIMS = r"\b(approved|funded|authori[sz]ed)\b"  # fake approvals; allowed where funding is the topic


def output_problems(summary: str | None, street_name: str | None, cross_street: str | None,
                    allow_funding_words: bool = False, max_chars: int = MAX_SUMMARY_CHARS) -> list[str]:
    """Reasons the agent's output fields can't be passed on as-is. Empty list = clean.

    The summary is the only free text that travels to the next agent and to agency alerts, so it
    must read like a plain description of a problem: short, no links, no money, no instructions.
    """
    problems = []
    if summary and len(summary) > max_chars:
        problems.append(f"text is longer than {max_chars} characters")
    for found in injection_flags(summary or ""):
        problems.append(f'summary contains instruction-like text: "{found}"')
    for pattern in OUTPUT_EXTRA_PATTERNS + ([] if allow_funding_words else [FUNDING_CLAIMS]):
        m = re.search(pattern, summary or "", flags=re.I)
        if m:
            problems.append(f'summary contains "{m.group(0)}"')
    for label, value in (("street name", street_name), ("cross street", cross_street)):
        if value and not re.fullmatch(STREET_FORMAT, value):
            problems.append(f"{label} is not a plain street name: {value[:60]!r}")
    return problems


# ---------- Fairness: keep protected characteristics and group blame out of what the system writes ----------

# Characteristics an agent should not bring into a description of a problem. Matching is on whole words.
# Disability and age are left out on purpose: ramps, accessibility, and older residents at risk are real
# 311 and safety topics.
PROTECTED_TERMS = [
    r"black (people|man|men|woman|women|kids|teens?|teenagers|residents|family|families|guys?)",
    r"white (people|man|men|woman|women|kids|teens?|teenagers|residents|family|families|guys?)",
    r"african[- ]americans?", r"hispanics?", r"latin[oaxe]s?", r"asians?", r"mexicans?", r"dominicans?",
    r"puerto ricans?", r"haitians?", r"jamaicans?", r"arabs?", r"africans?", r"chinese", r"koreans?",
    r"indians?", r"pakistanis?", r"bangladeshis?", r"russians?", r"jews|jewish", r"muslims?", r"christians?",
    r"hindus?", r"sikhs?", r"immigrants?", r"migrants?", r"refugees?", r"undocumented", r"gays?", r"lesbians?",
    r"transgender|trans (people|woman|women|man|men)", r"queer",
]
PROTECTED = re.compile(r"\b(" + "|".join(PROTECTED_TERMS) + r")\b", re.I)
# Dehumanizing labels for groups of people: never written by the system, whatever the request says.
DEHUMANIZING = re.compile(r"\b(illegals|illegal aliens?|those people|these people|thugs)\b", re.I)
GROUP_BLAME = re.compile(
    r"\b(too many|full of|overrun (by|with)|get rid of|kick out|ruin(ing|ed|s)?|because of (the|all)?\s*)\s*"
    r"(the |all |these |those )?(\w+ ){0,2}(" + "|".join(PROTECTED_TERMS) + r"|illegals)\b", re.I)
# A characteristic may be repeated when it names a business or place ("a Chinese restaurant", "the mosque's
# sidewalk"), not when it describes people.
PLACE_AFTER = re.compile(r"^\W*(\w+\W+)?(restaurant|store|shop|market|bakery|deli|grocery|supermarket|takeout|"
                         r"food|church|mosque|synagogue|temple|school|center|centre|cafe|bar|club|business|"
                         r"cuisine|community center)s?\b", re.I)


PERSON_AFTER = re.compile(r"^\W*(\w+\W+)?(people|persons?|man|men|woman|women|kids?|child|children|teens?|"
                          r"teenagers?|youths?|boys?|girls?|guys?|residents?|famil(y|ies)|neighbou?rs?|tenants?|"
                          r"workers?|owners?|landlords?|crowds?|groups?|gangs?|community|folks|ladies|gentlemen)\b", re.I)
GROUP_NOUNS = re.compile(r"(immigrants?|migrants?|refugees?|undocumented|jews|muslims|christians|hindus|sikhs|gays|"
                         r"lesbians|hispanics|latin[oaxe]s|asians|mexicans|dominicans|puerto ricans|haitians|"
                         r"jamaicans|arabs|africans|african[- ]americans|koreans|indians|pakistanis|bangladeshis|"
                         r"russians|transgender|queer)$", re.I)


def _about_people(text: str, m: re.Match) -> bool:
    """Does this match describe people? "Muslims", "immigrants", "Chinese men": yes. "Chinese elms",
    "a Korean restaurant": no (a tree species, a business)."""
    word = m.group(0)
    if PLACE_AFTER.match(text[m.end():]):
        return False
    return bool(GROUP_NOUNS.fullmatch(word) or re.match(r"(black|white) ", word, re.I)
                or PERSON_AFTER.match(text[m.end():]))


def protected_terms(text: str) -> set[str]:
    """Characteristics used to describe people in this text."""
    text = text or ""
    return {m.group(0).lower() for m in PROTECTED.finditer(text) if _about_people(text, m)}


def fairness_problems(output: str, source: str) -> list[str]:
    """Problems with what an agent wrote: protected characteristics the source material did not mention,
    or dehumanizing labels for people. A characteristic the source did mention (for example "a Chinese
    restaurant") may be repeated."""
    problems = []
    added = protected_terms(output) - protected_terms(source)
    if added:
        problems.append("mentions people's characteristics the request did not: " + ", ".join(sorted(added)))
    repeated = protected_terms(output) - added
    if repeated:
        problems.append("describes people by characteristics (describe the condition instead): "
                        + ", ".join(sorted(repeated)))
    m = DEHUMANIZING.search(output or "")
    if m:
        problems.append(f'uses a dehumanizing label for people: "{m.group(0)}"')
    return problems


def group_blame(text: str) -> str | None:
    """A phrase in a request that blames or targets a group of people, or None."""
    m = GROUP_BLAME.search(text or "") or DEHUMANIZING.search(text or "")
    return m.group(0) if m else None
