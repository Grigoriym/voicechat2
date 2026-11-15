"""Re-runs the grammar-corrector and Check prompts against real-session
sentences on live models, to compare models or validate a prompt change.

    .venv/bin/python scripts/bench_prompts.py corrector gemma2:9b claude:haiku
    .venv/bin/python scripts/bench_prompts.py check claude:sonnet

Uses the production prompts and complete() from voicechat2.py, so it tests
exactly what the app sends. Needs Ollama (local models) and/or the `claude`
CLI logged in (claude:* models). Not a pytest: it hits live models and the
scores are for a human to read.

Read the printed outputs, not just the score. The pass/fail check only
looks at OK-vs-correction; the 2026-11-15 "ignore capitalization" change
scored 16/16 while making every correction come back all lowercase.
"""

import asyncio
import importlib.util
import pathlib
import sys

# (text, expect) — "OK" = correct German, must not be flagged; "FIX" = has a
# real mistake. Mostly verbatim from real sessions, including the AI's lines.
CASES = [
    ("Hallo! Wie geht es dir denn heute?", "OK"),
    ("Hallo, was hast du denn vor?", "OK"),
    (
        "Interessant! Was für ein Algorithmus ist das denn genau? "
        "Hast du schon Erfahrung mit solchen Aufgaben?",
        "OK",
    ),
    ("Ups, sorry. Ich wollte fragen, welche deiner Apps dir am liebsten ist und warum.", "OK"),
    ("mir geht es gut danke das wetter draußen ist sehr schön", "OK"),
    ("ich mag sie alle", "OK"),
    ("Hallo, wie geht es dir? Was kannst du mir heute etwas interessantes erzählen?", "FIX"),
    ("Ach ja, Triple Sum! Da muss man schon ganz schön genau hinkugucken.", "FIX"),
    (
        "mir geht es gut danke das wetter draußen ist sehr schön aber es ist november "
        "und ist es kalt das ist der grund bin ich heute zu hause",
        "FIX",
    ),
    ('Die Name des Algorithmus is "Triple Sum", veielleicht weisst du shon es.', "FIX"),
    (
        "Ich habe deine letzte Frage nicht anverstanden, was du meinst beim "
        "Hast du davon eine besonders gerne benutzt?",
        "FIX",
    ),
    ("Gestern ich bin ins Kino gegangen.", "FIX"),
    ("Ich habe keine Lust, aber ich muss zu die Arbeit gehen.", "FIX"),
    (
        "Klar, gern! Was interessiert dich denn besonders an der Geschichte? "
        "HISTORICAL FICTION MAG SCHLAGWORTE ?",
        "FIX",
    ),
    (
        "hallo kannst du mir etwas interessantes aus aus geschichte erzählen und "
        "vielleicht wir können darüber diskutieren",
        "FIX",
    ),
]


def load_app():
    path = pathlib.Path(__file__).resolve().parent.parent / "voicechat2.py"
    spec = importlib.util.spec_from_file_location("voicechat2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(mode: str, models: list[str]) -> None:
    vc = load_app()
    for model in models:
        good = 0
        for text, expect in CASES:
            if mode == "corrector":
                result = await vc.check_grammar(text, model)
                got = None if result is None else ("OK" if result["correct"] else "FIX")
                shown = result and (result["corrected"] or "OK")
            else:
                result = await vc.check_text([{"role": "user", "content": text}], model)
                got = None if result is None else ("FIX" if result["mistakes"] else "OK")
                shown = result and "\n      ".join(
                    [f"{m['wrong']} → {m['right']}: {m['why']}" for m in result["mistakes"]]
                    + [f"Corrected: {result['corrected']}"]
                )
            good += got == expect
            mark = "pass" if got == expect else "FAIL"
            print(f"[{mark}] {model} want {expect}: {text[:70]}\n      {shown}")
        print(f"=== {model}: {good}/{len(CASES)}\n")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("corrector", "check"):
        sys.exit(__doc__)
    asyncio.run(run(sys.argv[1], sys.argv[2:]))
