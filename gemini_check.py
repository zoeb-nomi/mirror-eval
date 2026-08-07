#!/usr/bin/env python3
"""Which Gemini models does THIS key actually have, and which can ground?"""
import os, sys, json, httpx
sys.path.insert(0, "src"); import engines          # loads .env
K = os.environ["GEMINI_API_KEY"]; H = {"x-goog-api-key": K, "Content-Type": "application/json"}

r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", headers=H, timeout=60)
print("models.list HTTP", r.status_code)
if r.status_code != 200:
    print(r.text[:400]); raise SystemExit(1)
names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
         if "generateContent" in (m.get("supportedGenerationMethods") or [])]
print(f"\n{len(names)} models support generateContent:")
for n in names: print("  ", n)

cands = [n for n in names if n.startswith(("gemini-2.5-flash", "gemini-flash", "gemini-2.5-pro"))][:6]
print(f"\nTesting grounding on {len(cands)} candidates:\n")
for n in cands:
    for label, body in (("plain",   {"contents":[{"parts":[{"text":"Say OK"}]}]}),
                        ("grounded",{"contents":[{"parts":[{"text":"Who is Zoeb Nomi, the product manager?"}]}],
                                     "tools":[{"google_search":{}}]})):
        try:
            rr = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{n}:generateContent",
                            headers=H, json=body, timeout=90)
            note = ""
            if rr.status_code == 200 and label == "grounded":
                gm = (rr.json().get("candidates") or [{}])[0].get("groundingMetadata") or {}
                note = f"  chunks={len(gm.get('groundingChunks') or [])}"
            elif rr.status_code != 200:
                note = "  " + (rr.json().get("error", {}).get("message", "")[:90])
            print(f"  {n:<34} {label:<9} HTTP {rr.status_code}{note}")
        except Exception as e:
            print(f"  {n:<34} {label:<9} ERR {type(e).__name__}")
