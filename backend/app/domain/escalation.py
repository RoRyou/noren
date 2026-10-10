"""店員への引き継ぎ：理由コード、強制条件、要約の組み立て。

要約は 3 つの部品だけで作る：理由 + お客様の発言（日本語訳）+ システム上の注文の事実。
出どころのない内容は書かず、「未確認」とする（AI に推測させない）。
"""

from __future__ import annotations

from enum import StrEnum

from app.domain.models import LineStatus, Order, TableSession


class Reason(StrEnum):
    not_understood = "not_understood"  # 2 回続けて聞き取れない
    severe_allergy = "severe_allergy"
    complaint = "complaint"
    wrong_dish = "wrong_dish"  # 誤配膳
    foreign_object = "foreign_object"  # 異物
    bill_dispute = "bill_dispute"  # 会計の誤り
    abnormal_quantity = "abnormal_quantity"
    medical = "medical"  # 急病
    dietary_check = "dietary_check"  # △ / ？ の確認依頼
    other = "other"


URGENT = {Reason.severe_allergy, Reason.medical, Reason.foreign_object}

REASON_JA = {
    Reason.not_understood: "聞き取りが 2 回続けてできなかった",
    Reason.severe_allergy: "重いアレルギーの申告",
    Reason.complaint: "苦情",
    Reason.wrong_dish: "誤配膳の申告",
    Reason.foreign_object: "異物混入の申告",
    Reason.bill_dispute: "会計内容への疑問",
    Reason.abnormal_quantity: "数量が多すぎる注文",
    Reason.medical: "体調不良・急病の可能性",
    Reason.dietary_check: "食事制限の確認依頼",
    Reason.other: "その他",
}

# 急病のキーワード（お客様の発言の文字起こしに出たら、AI の判断を待たずに緊急で引き継ぐ）
MEDICAL_KEYWORDS = [
    "息が苦しい", "呼吸", "救急車", "倒れ", "意識", "アナフィラキシー", "エピペン", "じんましん", "喉が腫れ",
    "can't breathe", "cannot breathe", "ambulance", "anaphyla", "epipen", "passed out", "collapsed", "throat is swelling",
]


def detect_medical(utterance: str) -> bool:
    text = utterance.lower()
    return any(k.lower() in text for k in MEDICAL_KEYWORDS)


def order_facts(session: TableSession, orders: list[Order]) -> str:
    served = [f"{l.name.ja}×{l.qty}" for o in orders for l in o.lines if l.status == LineStatus.served]
    pending = [f"{l.name.ja}×{l.qty}" for o in orders for l in o.lines
               if l.status in (LineStatus.accepted, LineStatus.confirmed, LineStatus.cooking)]
    return f"提供済み：{'、'.join(served) or 'なし'}／未提供：{'、'.join(pending) or 'なし'}"


def build_summary(table_no: str, reason: Reason, utterance_ja: str, session: TableSession, orders: list[Order],
                  unknown_facts: list[str] | None = None) -> str:
    """店員向けの日本語の要約。"""
    parts = [f"{table_no}番：{REASON_JA[reason]}"]
    if utterance_ja:
        parts.append(f"お客様の発言（訳）「{utterance_ja}」")
    parts.append(order_facts(session, orders))
    for fact in unknown_facts or []:
        parts.append(f"{fact}は未確認")
    return "。".join(parts)
