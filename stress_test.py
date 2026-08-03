#!/usr/bin/env python3
"""GAUN Chatbot — Uçtan Uca (E2E) Stres Testi (Faz 16).

'Kaotik Öğrenci Simülasyonu': 7 zorlu soruyu canlı /api/chat endpoint'ine
gönderir, cevabı ve telemetri kaydını (gaunai_telemetry.db) doğrular.

Kapsanan rotalar: chitchat, structural (SQL), semantic (RAG), live.
Not: Senaryo 7, kullanıcı kararıyla DDGS web fallback yerine 'halüsinasyon yok'
kontrolüdür — bilinmeyen bilgide sistem uydurmamalı, 'Bilmiyorum' demeli.

Sunucu çalışırken çalıştır:
  ./.venv/bin/uvicorn api:app --port 8000   (ayrı terminalde)
  ./.venv/bin/python stress_test.py
"""

import sqlite3
import sys
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000"
DB = str(Path(__file__).resolve().parent / "gaunai_telemetry.db")
results: "list[tuple[str, bool, str]]" = []


def post(question: str, history=None) -> dict:
    r = requests.post(f"{BASE}/api/chat",
                      json={"question": question, "history": history or []},
                      timeout=200)
    r.raise_for_status()
    return r.json()  # {"answer", "log_id"}


def get_log(log_id: int) -> dict:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM chat_logs WHERE id = ?", (log_id,)).fetchone()
    con.close()
    return dict(row) if row else {}


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def _norm(s: str) -> str:
    return (s or "").lower()


def main() -> int:
    print("=== GaunAI E2E Stres Testi (Kaotik Öğrenci Simülasyonu) ===")
    try:
        requests.get(f"{BASE}/api/health", timeout=5).raise_for_status()
    except Exception as exc:
        print(f"HATA: sunucuya ulaşılamadı ({BASE}). Önce uvicorn'u başlat. {exc}")
        return 2

    logs = {}

    # 1) Chitchat — arama yok, kaynak linki yok
    d = post("selam naber?")
    lg = get_log(d["log_id"])
    logs["chitchat"] = lg
    check("1) Chitchat (anında, kaynaksız)",
          "🔗 Kaynak" not in d["answer"] and lg.get("intent_route") == "chitchat",
          f"route={lg.get('intent_route')}")

    # 2) Yapısal (SQL) — MariaDB'den doğru personel
    d2 = post("Veli Örnek dahili numarası?")
    lg2 = get_log(d2["log_id"])
    logs["structural"] = lg2
    check("2) Yapısal/SQL (Veli Örnek → 9001)",
          "9001" in d2["answer"] and lg2.get("intent_route") == "structural",
          f"route={lg2.get('intent_route')}")

    # 3) Cache (hız) — birebir tekrar, response_time_ms < 10
    d3 = post("Veli Örnek dahili numarası?")
    lg3 = get_log(d3["log_id"])
    check("3) Cache Hit (< 10 ms)",
          lg3.get("response_time_ms", 999) < 10 and lg3.get("intent_route") == "structural",
          f"{lg3.get('response_time_ms')} ms")

    # 4) Jargon + Semantik — resmi dile çeviri + RAG
    d4 = post("Ortalamam yetmiyor, okuldan atılır mıyım?")
    lg4 = get_log(d4["log_id"])
    logs["semantic"] = lg4
    rew4 = _norm(lg4.get("rewritten_question"))
    # NOT: 'İlişik'.lower() 'i̇' (i + birleşik nokta) üretir — 'ilişik' ile EŞLEŞMEZ.
    # Türkçe büyük İ içermeyen kelimelerle doğrula (bu tuzağa 3. düşüş olmasın).
    check("4) Jargon→Resmi + Semantik/RAG",
          lg4.get("intent_route") == "semantic"
          and ("kesme" in rew4 or "gano" in rew4 or "ortalama" in rew4),
          f"route={lg4.get('intent_route')} | çeviri='{lg4.get('rewritten_question')}'")

    # 5) Hafıza (context) — takip sorusu bağlamı korumalı
    hist = [
        {"role": "user", "content": "Ortalamam yetmiyor, okuldan atılır mıyım?"},
        {"role": "assistant", "content": d4["answer"]},
    ]
    d5 = post("Peki yaz okulunda ne yapacağım?", hist)
    lg5 = get_log(d5["log_id"])
    rew5 = _norm(lg5.get("rewritten_question"))
    check("5) Hafıza/Context (yaz okulu bağlamı)",
          "yaz okul" in rew5 or "yaz-okul" in rew5,
          f"çeviri='{lg5.get('rewritten_question')}'")

    # 6) Canlı Veri (Live) — Qdrant'a bakmadan siteden çekim
    d6 = post("Bugün yemekte ne var?")
    lg6 = get_log(d6["log_id"])
    logs["live"] = lg6
    check("6) Canlı Veri (Live route)",
          lg6.get("intent_route") == "live",
          f"route={lg6.get('intent_route')}")

    # 7) Halüsinasyon yok — bilinmeyen bilgide uydurmayıp ilgili birime yönlendirmeli
    d7 = post("Sultan Fatih Fakültesi dekanı kimdir?")
    lg7 = get_log(d7["log_id"])
    ans7 = _norm(d7["answer"])   # .lower() Türkçe 'ü'yü korur → 'güncel' ile eşle
    # 2026-07-22: match_department artık deterministik olarak "böyle bir birim
    # yok" diyor (unit_not_found_message) — eski belirsiz "güncel bilgim yok"
    # yönlendirmesinden daha net bir halüsinasyon-karşıtı davranış.
    check("7) Halüsinasyon Yok (nazik birim yönlendirmesi)",
          "bu isimde bir birim" in ans7 and "gaziantep.edu.tr" in ans7,
          f"route={lg7.get('intent_route')} | cevap='{d7['answer'][:70]}'")

    # 8) Sosyal (Learning fazı) — kısmi zamanlı çalışma SKS cevabına topraklanmalı
    d8 = post("Okulda part-time çalışmak istiyorum, nasıl başvururum?")
    lg8 = get_log(d8["log_id"])
    ans8 = _norm(d8["answer"])
    check("8) Sosyal/SKS (kısmi zamanlı çalışma)",
          "sks" in ans8 and "bilmiyorum" not in ans8,
          f"route={lg8.get('intent_route')} | cevap='{d8['answer'][:70]}'")

    # 9) Mezuniyet (Learning fazı) — kayıp diploma: gazete ilanı + OİDB prosedürü
    d9 = post("Diplomamı kaybettim, yenisini nasıl çıkartırım?")
    lg9 = get_log(d9["log_id"])
    ans9 = _norm(d9["answer"])
    check("9) Mezuniyet/OİDB (ikinci nüsha diploma)",
          "gazete" in ans9 and "bilmiyorum" not in ans9,
          f"route={lg9.get('intent_route')} | cevap='{d9['answer'][:70]}'")

    # 10) Mezuniyet (Dijital Kampüs seti) — e-Devlet geçici mezuniyet belgesi zamanı
    d10 = post("e-Devlet mezun belgesi ne zaman çıkar?")
    lg10 = get_log(d10["log_id"])
    ans10 = _norm(d10["answer"])
    check("10) Mezuniyet (e-Devlet belge zamanı)",
          "devlet" in ans10 and ("onay" in ans10 or "mezuniyet" in ans10)
          and "bilmiyorum" not in ans10,
          f"route={lg10.get('intent_route')} | cevap='{d10['answer'][:70]}'")

    # 11) Teknik (Dijital Kampüs seti) — eduroam kaydı hesap.gaziantep.edu.tr'den
    d11 = post("eduroam şifremi nasıl alırım?")
    lg11 = get_log(d11["log_id"])
    ans11 = _norm(d11["answer"])
    check("11) Teknik/BİDB (eduroam hesap kaydı)",
          "hesap.gaziantep.edu.tr" in ans11 and "bilmiyorum" not in ans11,
          f"route={lg11.get('intent_route')} | cevap='{d11['answer'][:70]}'")

    # --- Telemetri: 4 rota da kaydedilmiş mi? ---
    print("\n=== Telemetri (chat_logs) doğrulama ===")
    seen = {lg.get("intent_route") for lg in logs.values()}
    check("Telemetri: 4 rota (chitchat/structural/semantic/live) kayıtlı",
          {"chitchat", "structural", "semantic", "live"}.issubset(seen),
          f"kayıtlı rotalar={sorted(seen)}")
    check("Telemetri: jargon çevirisi kaydedildi (4. senaryo)",
          _norm(logs["semantic"].get("rewritten_question")) not in ("", _norm("Ortalamam yetmiyor, okuldan atılır mıyım?")),
          f"'{logs['semantic'].get('rewritten_question')}'")

    # --- Özet ---
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== SONUÇ: {passed}/{len(results)} PASS ===")
    fails = [n for n, ok, _ in results if not ok]
    if fails:
        print("Başarısız:", ", ".join(fails))
        return 1
    print(f"Sistem {len(results)} zorlu virajı SIFIR HATAYLA geçti. ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
