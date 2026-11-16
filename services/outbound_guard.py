#!/usr/bin/env python3
"""outbound_guard.py: looks at what the agent is about to send to the internet, before it goes.

Everything that leaves goes through here first: OpenClaw's web_search and web_fetch (plugins/outbound-guard asks this
service) and the scheduled-research searches (services/overnight.py imports check()). What it looks for:

  * the identifiers of HIPAA's Safe Harbor list (45 CFR 164.514(b)(2)), by the letter used there: see IDENTIFIERS
  * a person's own health ("my chemo", "I have diabetes"), not health as a topic ("symptoms of diabetes")
  * whatever you listed as private in config/private-terms.txt (names, your county, a project: one per line)
  * data tucked into a web address: a very long value, or an identifier inside base64 or hex

It checks what a request SAYS, never its topic: "how to build an underground bunker" passes; adding your county to
private-terms.txt makes "bunker permits Lincoln County" stop and ask. A hit is not an error: the agent's run pauses and
you see the exact words and choose (the plugin does this), or, with nobody there (scheduled research), it is not sent.

  POST /check {"tool": "web_search", "text": "...", "url": false}  {"action": "allow"|"ask", "found": [{"id", "label"}]}
  GET  /health                                                     {"status": "ok", "terms": <count>, "ask_all": false}

  python3 services/outbound_guard.py check "words"      one check, printed
  python3 services/outbound_guard.py selftest           the test cases at the bottom of this file

Behaviour worth knowing:
  * Listens on 127.0.0.1 only ($OUTBOUND_GUARD_PORT, default 18892). If it is down the plugin blocks, it doesn't wave through.
  * Its log ($STATE_DIR/outbound.log) records time, tool, website, what was found and the outcome. Never the words themselves.
  * OUTBOUND_ASK_ALL=on asks before every search and page, identifier or not: nothing detects everything, a person does better.
    Scheduled research has nobody to ask, so with it on that job searches nothing. OUTBOUND_GUARD=off lets everything through.
  * This is a safety net, not a guarantee. It matches patterns, so it can't recognise a stranger's name or a detail worded in
    a way it has never seen, and P and Q (biometrics, face photos) aren't text. docs/SECURITY.md says what it doesn't cover.
"""
import base64, binascii, json, os, re, sys, threading, urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("OUTBOUND_GUARD_PORT", "18892"))
STATE_DIR = os.path.expanduser(os.environ.get("STATE_DIR", "~/.local/state/local-agent"))
LOG = os.path.join(STATE_DIR, "outbound.log")
TERMS_FILE = os.environ.get("PRIVATE_TERMS_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "private-terms.txt")
ASK_ALL = os.environ.get("OUTBOUND_ASK_ALL", "off") == "on"
ENABLED = os.environ.get("OUTBOUND_GUARD", "on") != "off"
LONG_VALUE = 120      # a web-address value this long is more likely data than a search term
MAX_TEXT = 20000      # far longer than any search or address; the rest is not read, so a longer one always asks

# 45 CFR 164.514(b)(2)(i), by its letters (E, fax, is found with D; N, web addresses, is what check_url looks inside).
# P and Q are not text. The entries in words are extra, not on the list.
IDENTIFIERS = {
    "A": "a person's name", "B": "an address or home location", "C": "a personal date or an age over 89",
    "D": "a phone or fax number", "F": "an email address", "G": "a Social Security number",
    "H": "a medical record number", "I": "a health plan number", "J": "an account or card number",
    "K": "a licence or certificate number", "L": "a vehicle number", "M": "a device number",
    "O": "an IP address", "R": "a personal ID or code", "HEALTH": "your own health details",
    "PRIVATE": "something on your private list", "ENCODED": "data hidden in a web address", "TOOLONG": "too much text to check",
}

NUM = r"(?:\s*(?:number|num|no\.?|\#|id))?(?:\s+is)?[\s:#=-]*"      # "number", "no.", "#" and so on before the value
CODE = r"(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,}"           # 5+ characters with a digit, so plain words and years don't match
RELATION = (r"wife|husband|spouse|partner|son|daughter|child|kid|baby|mother|mom|mum|father|dad|sister|brother|grandmother|"
            r"grandfather|grandma|grandpa|aunt|uncle|cousin|friend|neighbou?r|boss|coworker|doctor|dr\.?|therapist|psychiatrist|"
            r"nurse|surgeon|dentist|lawyer|attorney|patient|client")
SELF = r"\b(?:i|i'm|im|i've|ive|i'd|me|my|mine|we|we're|our|us|myself)\b|(?<!\ba )(?<!\ban )(?<!\bthe )(?<!\byour )\b(?:" + RELATION + r")(?:'s)?\b"
MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE = r"(?:" + MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{2,4})?|\d{1,2}\s+" + MONTH + r"(?:\s+\d{2,4})?|\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?)"
HEALTH = (r"diagnos\w*|prognosis|chemo\w*|radiation therapy|dialysis|biopsy|surgery|prescri\w*|medication|meds|dosage|"
          r"cancer|tumou?r|leukemia|lymphoma|diabet\w*|insulin|hiv|aids|std|sti|herpes|hepatitis|asthma|epilep\w*|seizures?|stroke|"
          r"heart (?:attack|failure|disease)|alzheimer\w*|dementia|parkinson\w*|autis\w*|adhd|depression|anxiety|bipolar|"
          r"schizo\w*|ptsd|suicid\w*|self[- ]harm|eating disorder|anorexia|bulimia|addict\w*|alcoholi\w*|overdose|rehab|"
          r"miscarriage|abortion|pregnan\w*|infertil\w*|ivf|menopause|erectile|lab results|blood test|mri results?|"
          r"antidepressants?|lexapro|zoloft|prozac|xanax|adderall|ozempic|metformin|lisinopril|warfarin|oxycodone|methadone")

I = re.I
PATTERNS = [
    ("A", re.compile(r"\b(?i:my|our)\s+(?i:" + RELATION + r")[\s,]+(?:(?i:is|named|called)\s+)?[A-Z][a-z]+")),
    ("A", re.compile(r"\b(?i:patient|client|resident)\s+[A-Z][a-z]+\s+[A-Z][a-z]+")),
    ("B", re.compile(r"\b\d{1,6}\s+(?:[A-Za-z0-9.'-]+\s+){1,4}(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|circle|cir|"
                     r"way|place|pl|highway|hwy|parkway|pkwy|trail|trl|terrace|ter)\b", I)),
    ("B", re.compile(r"\bp\.?\s?o\.?\s+box\s+\d+", I)),
    ("B", re.compile(r"\b\d{5}-\d{4}\b")),
    ("B", re.compile(r"\bzip(?:\s*code)?[\s:#=-]*\d{5}\b", I)),
    ("B", re.compile(r"\b[A-Z]{2},?\s+\d{5}\b")),
    ("B", re.compile(r"\b(?:i|we)\s+(?:live|reside|stay|am staying|are staying|am located|are located)\s+(?:in|at|on|near)\b", I)),
    ("B", re.compile(r"\bmy\s+(?:home\s+|street\s+|mailing\s+)?address\b", I)),
    ("B", re.compile(r"\b(?:my|our)\s+(?:\d+\s+acres?|acreage|home|house|land|lot|property|farm|ranch|cabin|bunker|shelter)\s+(?:in|at|on|near)\b", I)),
    ("D", re.compile(r"(?<![\d.])(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\d.])")),
    ("D", re.compile(r"(?<![\w.])\+\d{1,3}[\s.-]?\d[\d\s.-]{6,13}\d(?!\d)")),
    ("F", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")),
    ("F", re.compile(r"\b[\w.+-]+\s+(?:at|\[at\]|\(at\))\s+[\w-]+\s+(?:dot|\[dot\]|\(dot\))\s+(?:com|org|net|edu|gov|io|co|me)\b", I)),
    ("G", re.compile(r"(?<![\d-])(?!000|666|9\d\d)\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}(?![\d-])")),
    ("G", re.compile(r"\b(?:ssn|social security|social)" + NUM + r"\d{9}\b", I)),
    ("H", re.compile(r"\b(?:mrn|medical record|chart|patient)" + NUM + CODE, I)),
    ("I", re.compile(r"\b(?:member|subscriber|beneficiary|policy|group|insurance|medicare|medicaid)" + NUM + CODE, I)),
    ("I", re.compile(r"\b\d[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d-?[AC-HJKMNP-RT-Y][AC-HJKMNP-RT-Y0-9]\d-?[AC-HJKMNP-RT-Y]{2}\d{2}\b")),   # Medicare number
    ("J", re.compile(r"\b(?:account|acct|routing|bank)" + NUM + r"\d[\d -]{3,}", I)),
    ("J", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")),                                                                               # IBAN
    ("K", re.compile(r"\b(?:driver'?s?\s+)?(?:licen[sc]e|passport|certificate|permit|dea|npi)" + NUM + CODE, I)),
    ("L", re.compile(r"\b(?:license plate|licence plate|number plate|plate)" + NUM + CODE, I)),
    ("M", re.compile(r"\b(?:serial|imei|device id|device)" + NUM + CODE, I)),
    ("M", re.compile(r"\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b", I)),
    ("O", re.compile(r"\b(?:[0-9a-f]{1,4}:){7}[0-9a-f]{1,4}\b", I)),
    ("R", re.compile(r"\b(?:my|our)\s+(?:[\w-]+\s+){0,3}(?:id|number|code|pin|password|passcode)\s*(?:is|:|=)\s*\S{4,}", I)),
    ("R", re.compile(r"\b(?:(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)[\s-]+){6,}(?:zero|one|two|three|four|five|six|seven|eight|nine|oh)\b", I)),   # a number spelled out
]
CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])")
VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
IPV4 = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\d.])")
OLD_AGE = re.compile(r"\b(?:9\d|1[01]\d)[- ]?(?:years?|yrs?|y/?o)\b", I)
HEALTH_RE = re.compile(r"\b(?:" + HEALTH + r")\b", I)
SELF_RE = re.compile(SELF, I)
# How a general question opens: "how do I talk to someone about depression" says nothing about the person asking.
QUESTION = re.compile(r"\b(?:how (?:do|can|should|would|could) (?:i|we)|(?:can|could|should|would|do|may) (?:i|we))\b", I)
LIFE_EVENT = re.compile(r"\b(?:born|birth\s?date|date of birth|dob|birthday|admitted|discharged|admission|discharge|died|date of death|deceased)\b\D{0,20}(" + DATE + r")(\s*bc\b)?", I)
FULL_NAME = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")
NOT_A_NAME = re.compile(r"\b(?:Clinic|Hospital|Society|Association|Foundation|Institute|University|Center|Centre|Health|Medical|Medicine|Disease|Syndrome|College|School|Journal|News|Times|Mayo|Cleveland|Johns|American|National|United|New|North|South|East|West)\b")
PERSONAL_KEY = re.compile(r"^(?:name|first_?name|last_?name|full_?name|user|username|patient|email|e_?mail|dob|ssn|phone|tel|mobile|address|addr|zip|zipcode)$", I)
OPAQUE = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
URL_IN_TEXT = re.compile(r"""(?:https?://|www[.])[^ <>"']+""", I)
HEXED = re.compile(r"\b[0-9a-fA-F]{32,}\b")


def luhn(digits):
    total = 0
    for i, c in enumerate(reversed(digits)):
        n = int(c) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def vin_ok(v):
    """The check digit every US vehicle number carries (position 9), so 17-character codes that aren't VINs don't match."""
    tr = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5, 7, 9, 2, 3, 4, 5, 6, 7, 8, 9]))
    weights = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]
    r = sum((int(c) if c.isdigit() else tr[c]) * w for c, w in zip(v, weights)) % 11
    return v[8] == ("X" if r == 10 else str(r))


def recent_life_event(m):
    """Birth and death dates count within living memory: Lincoln's birthday is history, not a record."""
    year = re.search(r"\b\d{4}\b", m.group(1))
    return not m.group(2) and not (year and int(year.group()) < 1900)


def public_ipv4(m):
    a = [int(x) for x in m.groups()]
    if max(a) > 255 or a[0] in (0, 10, 127) or (a[0], a[1]) in ((192, 168), (169, 254)) or (a[0] == 172 and 16 <= a[1] <= 31):
        return False
    return not re.search(r"(?:version|ver\.?|v|release|build)\s*$", m.string[:m.start()], I)


_terms, _terms_at, _terms_lock = [], None, threading.Lock()


def terms():
    """config/private-terms.txt, re-read whenever it changes: one word or phrase per line, # for comments."""
    global _terms, _terms_at
    try:
        at = os.stat(TERMS_FILE).st_mtime
    except OSError:
        at = 0
    with _terms_lock:
        if at != _terms_at:
            words = []
            if at:
                with open(TERMS_FILE, encoding="utf-8", errors="replace") as f:
                    words = [w.strip() for w in f if w.strip() and not w.lstrip().startswith("#")]
            _terms = [re.compile(r"(?<!\w)" + r"[\s_+.-]+".join(re.escape(p) for p in w.split()) + r"(?!\w)", I) for w in words]
            _terms_at = at
        return _terms


def scan(text):
    """The set of identifier letters in plain text."""
    hits = {k for k, p in PATTERNS if p.search(text)}
    if any(luhn(d) for d in (re.sub(r"\D", "", m.group()) for m in CARD.finditer(text)) if 13 <= len(d) <= 19):
        hits.add("J")
    if any(vin_ok(m.group()) and re.search(r"[A-Z]", m.group()) and re.search(r"\d", m.group()) for m in VIN.finditer(text)):
        hits.add("L")
    if any(public_ipv4(m) for m in IPV4.finditer(text)):
        hits.add("O")
    if any(recent_life_event(m) for m in LIFE_EVENT.finditer(text)):
        hits.add("C")
    if OLD_AGE.search(text) and SELF_RE.search(text):
        hits.add("C")
    for m in HEALTH_RE.finditer(text):                                 # the person and the condition, close together
        near = text[max(0, m.start() - 45):m.end() + 45]
        if SELF_RE.search(QUESTION.sub(" ", near)):
            hits.add("HEALTH")
        if any(not NOT_A_NAME.search(n.group()) for n in FULL_NAME.finditer(near)):
            hits.add("A")
    if any(t.search(text) for t in terms()):
        hits.add("PRIVATE")
    return hits


def unhide(token):
    """The text inside a base64 or hex string, or ''."""
    for decode in (lambda t: base64.urlsafe_b64decode(t + "=" * (-len(t) % 4)), lambda t: base64.b64decode(t + "=" * (-len(t) % 4)), bytes.fromhex):
        try:
            s = decode(token.replace("=", "")).decode("utf-8")
        except (ValueError, binascii.Error, UnicodeDecodeError):
            continue
        if len(s) >= 6 and all(c.isprintable() or c.isspace() for c in s):
            return s
    return ""


def check_url(url):
    """A web address itself is fine (that's what reading a page is). What it carries is what counts."""
    p = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    hits = set()
    if any(t.search(p.netloc) for t in terms()):
        hits.add("PRIVATE")
    query = urllib.parse.parse_qsl(p.query + "&" + p.fragment.replace("?", "&"), keep_blank_values=True)
    values = [v for _, v in query]
    parts = [urllib.parse.unquote(s) for s in p.path.split("/") if s]
    if any(v and PERSONAL_KEY.match(k) for k, v in query):             # ?patient=... is personal whatever the value looks like
        hits.add("R")
    hits |= scan(" ".join(values))                                     # a name in one value, a condition in another
    for v in values + parts:
        hits |= scan(v) | scan(re.sub(r"[-_+]+", " ", v))
        for m in list(OPAQUE.finditer(v)) + list(HEXED.finditer(v)):
            s = unhide(m.group())
            if s and scan(s):
                hits.add("ENCODED")
    if any(len(v) >= LONG_VALUE for v in values):
        hits.add("ENCODED")
    return hits


def check(text, url=False):
    """-> sorted identifier letters found in what is about to be sent."""
    text = text or ""
    hits = {"TOOLONG"} if len(text) > MAX_TEXT else set()
    text = text[:MAX_TEXT]
    if url:
        return sorted(hits | check_url(text))
    hits |= scan(text)
    # A web address inside a search or a command carries what a fetched one would. Seen with a real model: a web_fetch
    # this guard stopped (?patient=...&dob=...) was retried by the model as a web_search with the same address as the query.
    for m in URL_IN_TEXT.finditer(text):
        hits |= check_url(m.group().rstrip(".,;)"))
    return sorted(hits)


def log(tool, target, found, action):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s %s %s %s %s\n" % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), tool, target or "-", ",".join(found) or "clean", action))


def site(text, url):
    """For the log: the website of an address, never a search's words or an address's path."""
    if not url:
        return ""
    return urllib.parse.urlsplit(text if "://" in text else "https://" + text).hostname or ""


def decide(tool, text, url=False):
    found = check(text, url)
    action = "ask" if ENABLED and (found or ASK_ALL) else "allow"
    log(tool, site(text, url), found, action)
    return {"action": action, "found": [{"id": f, "label": IDENTIFIERS[f]} for f in found]}


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok", "terms": len(terms()), "ask_all": ASK_ALL})
        else:
            self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/check":
            return self.reply(404, {"error": "not found"})
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            self.reply(200, decide(str(d.get("tool") or "?"), str(d.get("text") or ""), bool(d.get("url"))))
        except (ValueError, TypeError) as e:
            self.reply(400, {"error": str(e)})

    def log_message(self, *a):
        pass                                                           # the words must never reach a log


# (what the agent would send, is it an address, the letters that must be found; [] = must pass untouched)
CASES = [
    ("how to build an underground bunker", False, []),
    ("symptoms of diabetes", False, []),
    ("average cost of chemotherapy", False, []),
    ("weather in Chicago", False, []),
    ("python 3.12 release notes", False, []),
    ("March 3 2024 earthquake", False, []),
    ("RFC 5737 documentation addresses", False, []),
    ("oldest person ever 115 years old", False, []),
    ("what does a phone number look like", False, []),
    ("nginx version 1.25.3.1 changelog", False, []),
    ("my computer won't boot after a driver update", False, []),
    ("https://en.wikipedia.org/wiki/Fallout_shelter", True, []),
    ("https://www.google.com/search?q=bunker+ventilation&hl=en", True, []),
    ("https://example.org/a/b/2024/03/report.pdf", True, []),
    ("my wife Mary needs a new car", False, ["A"]),
    ("patient John Smith discharge plan", False, ["A"]),
    ("1600 Pennsylvania Avenue parking", False, ["B"]),
    ("best roofers I live in Lincoln County", False, ["B"]),
    ("90210-1234 mail forwarding", False, ["B"]),
    ("date of birth March 3 1961 lookup", False, ["C"]),
    ("my 92-year-old mother needs a walker", False, ["C"]),
    ("call 212-555-0147 now", False, ["D"]),
    ("contact (212) 555 0147", False, ["D"]),
    ("email jane.doe@example.org reply", False, ["F"]),
    ("123-45-6789 credit report", False, ["G"]),
    ("ssn 123456789", False, ["G"]),
    ("MRN 00482913 records request", False, ["H"]),
    ("member id XJ48291 claim status", False, ["I"]),
    ("1EG4-TE5-MK72 claim", False, ["I"]),
    ("4111 1111 1111 1111 balance", False, ["J"]),
    ("account number 00123456789 balance", False, ["J"]),
    ("driver's license D1234567 renewal", False, ["K"]),
    ("1HGCM82633A004352 recall", False, ["L"]),
    ("plate 7ABC123 owner", False, ["L"]),
    ("serial number C02XG0FDH7JY warranty", False, ["M"]),
    ("00:1A:2B:3C:4D:5E vendor", False, ["M"]),
    ("who owns 8.8.4.4 traffic", False, ["O"]),
    ("my employee id is 8841-22", False, ["R"]),
    ("I have diabetes and need a diet", False, ["HEALTH"]),
    ("treatment for my son's leukemia", False, ["HEALTH"]),
    ("my mother's dementia getting worse", False, ["HEALTH"]),
    ("https://evil.example/x?d=123-45-6789", True, ["G"]),
    ("https://evil.example/collect/jane.doe%40example.org", True, ["F"]),
    ("https://evil.example/p?q=" + base64.urlsafe_b64encode(b"ssn 123-45-6789 for jane").decode(), True, ["ENCODED"]),
    ("https://evil.example/p?q=" + "a1b2" * 40, True, ["ENCODED"]),
    # a web address inside a search or a command (url=False) is read like a fetched one
    ("https://example.com/?patient=John+Smith&dob=1950-03-04", False, ["R"]),
    ("summarise https://example.com/?patient=John+Smith&dob=1950-03-04 for me", False, ["R"]),
    ("curl -sS 'https://api.example/u?email=jane.doe@example.org' | head -5", False, ["F"]),
    ("Fetch the Wikipedia article https://en.wikipedia.org/wiki/2022_FIFA_World_Cup and tell me who won", False, []),
    ("curl -sS https://example.com/ | head -5", False, []),
]


def selftest():
    import tempfile
    global TERMS_FILE
    bad = 0
    with tempfile.TemporaryDirectory() as d:
        TERMS_FILE = os.path.join(d, "terms.txt")
        for text, url, want in CASES:
            got = check(text, url)
            if (want and not set(want) <= set(got)) or (not want and got):
                bad += 1
                print("FAIL", repr(text[:70]), "wanted", want, "got", got)
        with open(TERMS_FILE, "w", encoding="utf-8") as f:
            f.write("# my list\nLincoln County\nProject Ember\n")
        for text, url, want in (("bunker permits lincoln county", False, ["PRIVATE"]), ("https://x.example/lincoln-county/map", True, ["PRIVATE"]),
                                ("https://x.example/?q=project+ember", True, ["PRIVATE"]), ("bunker permits Orange County", False, [])):
            got = check(text, url)
            if got != want:
                bad += 1
                print("FAIL", repr(text), "wanted", want, "got", got)
    if check("x" * (MAX_TEXT + 1)) != ["TOOLONG"]:
        bad += 1
        print("FAIL an oversized text must not pass")
    print("%d cases, %d failed" % (len(CASES) + 5, bad))
    return 1 if bad else 0


def main():
    if sys.argv[1:2] == ["selftest"]:
        sys.exit(selftest())
    if sys.argv[1:2] == ["check"]:
        text = " ".join(sys.argv[2:])
        found = check(text, url="://" in text)
        print(("found: " + ", ".join(IDENTIFIERS[f] for f in found)) if found else "nothing found")
        sys.exit(0)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("outbound guard on 127.0.0.1:%d (%d private terms, ask before everything: %s)" % (PORT, len(terms()), ASK_ALL), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
