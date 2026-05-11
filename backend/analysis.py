from __future__ import annotations

import math
import re
from statistics import mean, median
from typing import Any

from .data import companies, get_company, peers_for, research_feed


def round2(value: float) -> float:
    return round(float(value), 2)


def screen_companies(filters: list[dict[str, Any]], sort_by: str | None = None, descending: bool = True) -> list[dict[str, Any]]:
    rows = []
    for company in companies():
        row = {
            "ticker": company["ticker"],
            "name": company["name"],
            "sector": company["sector"],
            "industry": company["industry"],
            "price": company["price"],
            "targetPrice": company["targetPrice"],
            **company["metrics"],
        }
        if _matches_filters(row, filters):
            rows.append(row)
    if sort_by:
        rows.sort(key=lambda row: row.get(sort_by, -math.inf), reverse=descending)
    return rows


def _matches_filters(row: dict[str, Any], filters: list[dict[str, Any]]) -> bool:
    for item in filters:
        field = item.get("field")
        op = item.get("op", ">=")
        value = item.get("value")
        if field not in row or value in (None, ""):
            continue
        current = row[field]
        if not isinstance(current, (int, float)):
            continue
        target = float(value)
        if op == ">=" and not current >= target:
            return False
        if op == "<=" and not current <= target:
            return False
        if op == ">" and not current > target:
            return False
        if op == "<" and not current < target:
            return False
        if op == "=" and not abs(current - target) < 0.00001:
            return False
    return True


def analyze_earnings(text: str, ticker: str | None = None) -> dict[str, Any]:
    company = get_company(ticker or "") if ticker else None
    clean = " ".join(text.split())
    revenue = _extract_amount(clean, r"revenue(?:\s+of|\s+was|\s+were|\s+reached)?\s+\$?([0-9,.]+)\s*(billion|million|bn|m)?")
    eps = _extract_amount(clean, r"(?:diluted\s+)?eps(?:\s+of|\s+was)?\s+\$?([0-9,.]+)", money=False)
    margin = _extract_percent(clean, r"(?:operating|gross|net)\s+margin(?:\s+of|\s+was)?\s+([0-9.]+)%")
    revenue_estimate = _extract_amount(clean, r"(?:consensus|estimate|expected|street)\s+(?:revenue\s+)?(?:of|was)?\s*\$?([0-9,.]+)\s*(billion|million|bn|m)?")
    eps_estimate = _extract_amount(clean, r"(?:consensus|estimate|expected|street)\s+(?:eps\s+)?(?:of|was)?\s*\$?([0-9,.]+)", money=False)

    metrics = []
    if revenue:
        estimate = revenue_estimate or (company["revenue"] / 4 if company else revenue * 0.985)
        metrics.append(_metric_surprise("Revenue", revenue, estimate, "$B"))
    if eps:
        estimate = eps_estimate or (company["eps"] / 4 if company else eps * 0.97)
        metrics.append(_metric_surprise("EPS", eps, estimate, "$"))
    if margin:
        benchmark = company["operatingMargin"] if company else max(margin - 0.8, 1)
        metrics.append(_metric_surprise("Margin", margin, benchmark, "%"))

    lower = clean.lower()
    positive_terms = ["raise", "raised", "beat", "strong", "accelerat", "record", "expansion", "above consensus", "higher"]
    negative_terms = ["lower", "miss", "weak", "decline", "pressure", "headwind", "delay", "below consensus", "cautious"]
    pos = sum(lower.count(term) for term in positive_terms)
    neg = sum(lower.count(term) for term in negative_terms)
    score = pos - neg + sum(1 if metric["surprisePct"] > 0 else -1 for metric in metrics)
    tone = "constructive" if score > 1 else "cautious" if score < -1 else "balanced"
    verdict = {
        "constructive": "Beat quality looks healthy, with the strongest signal coming from growth or margin language.",
        "balanced": "The release is mixed: enough positives to keep the thesis alive, but not a clean all-clear.",
        "cautious": "The update points to estimate risk; watch margin pressure, demand language, and guidance cadence.",
    }[tone]

    risks = _risk_items(lower, company)
    changes = _change_items(metrics, lower)
    if not metrics:
        changes.insert(0, "No clean revenue or EPS line was detected, so the interpretation relies on qualitative language.")

    return {
        "ticker": company["ticker"] if company else ticker,
        "company": company["name"] if company else "Selected company",
        "tone": tone,
        "score": score,
        "verdict": verdict,
        "metrics": metrics,
        "keyChanges": changes[:5],
        "risks": risks[:5],
        "watchlist": [
            "Next-quarter revenue guide versus consensus",
            "Gross margin bridge and one-time cost adjustments",
            "Management commentary on demand durability",
            "Capital allocation: buybacks, debt paydown, and capex intensity",
        ],
        "confidence": min(94, max(58, 72 + len(metrics) * 6 + abs(score) * 2)),
    }


def _extract_amount(text: str, pattern: str, money: bool = True) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    if not money:
        return value
    unit = match.group(2).lower() if len(match.groups()) > 1 and match.group(2) else "billion"
    if unit in {"million", "m"}:
        return value / 1000
    return value


def _extract_percent(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _metric_surprise(label: str, actual: float, estimate: float, unit: str) -> dict[str, Any]:
    surprise = ((actual / estimate) - 1) * 100 if estimate else 0
    return {
        "label": label,
        "actual": round2(actual),
        "estimate": round2(estimate),
        "surprisePct": round2(surprise),
        "unit": unit,
        "status": "beat" if surprise > 1 else "miss" if surprise < -1 else "inline",
    }


def _change_items(metrics: list[dict[str, Any]], lower_text: str) -> list[str]:
    output = []
    for metric in metrics:
        status = metric["status"]
        if status == "beat":
            output.append(f"{metric['label']} beat by {metric['surprisePct']}%, which should support near-term estimate revisions.")
        elif status == "miss":
            output.append(f"{metric['label']} missed by {abs(metric['surprisePct'])}%, creating a near-term diligence item.")
        else:
            output.append(f"{metric['label']} was broadly in line, so investor focus shifts to guidance quality.")
    if "guidance" in lower_text and any(word in lower_text for word in ["raise", "raised", "above"]):
        output.append("Guidance language improved, which often matters more than the printed quarter.")
    if "margin" in lower_text and any(word in lower_text for word in ["pressure", "headwind", "lower"]):
        output.append("Margin commentary is the main pressure point and should be tracked in the next model update.")
    return output


def _risk_items(lower_text: str, company: dict[str, Any] | None) -> list[str]:
    risks = []
    keywords = {
        "margin": "Margin pressure could dilute operating leverage.",
        "inventory": "Inventory language suggests possible demand or pricing friction.",
        "regulatory": "Regulatory language can extend the time needed for thesis confirmation.",
        "fx": "Foreign-exchange pressure may reduce reported growth quality.",
        "competition": "Competitive intensity appears to be rising.",
        "capex": "Higher capex can reduce free-cash-flow conversion.",
    }
    for key, risk in keywords.items():
        if key in lower_text:
            risks.append(risk)
    if company and company["riskScore"] > 65:
        risks.append(f"{company['ticker']} already screens high-risk at {company['riskScore']:.1f}/100.")
    if not risks:
        risks.append("No acute risk phrase dominated the release; monitor guidance and margin bridge details.")
    return risks


def analyze_portfolio(holdings: list[dict[str, Any]]) -> dict[str, Any]:
    positions = []
    total_value = 0.0
    for holding in holdings:
        company = get_company(str(holding.get("ticker", "")).upper())
        if not company:
            continue
        shares = float(holding.get("shares") or 0)
        value = shares * company["price"]
        total_value += value
        positions.append({"company": company, "shares": shares, "value": value})

    enriched = []
    sector_weights: dict[str, float] = {}
    exposures = {"qualityScore": 0.0, "growthScore": 0.0, "valueScore": 0.0, "riskScore": 0.0, "beta": 0.0}
    for position in positions:
        company = position["company"]
        weight = position["value"] / total_value if total_value else 0
        sector_weights[company["sector"]] = sector_weights.get(company["sector"], 0) + weight
        for key in exposures:
            exposures[key] += company[key] * weight
        enriched.append(
            {
                "ticker": company["ticker"],
                "name": company["name"],
                "sector": company["sector"],
                "shares": position["shares"],
                "price": company["price"],
                "value": round2(position["value"]),
                "weight": round2(weight * 100),
                "beta": company["beta"],
                "riskScore": company["riskScore"],
            }
        )

    flags = []
    if enriched:
        top = max(enriched, key=lambda item: item["weight"])
        if top["weight"] > 30:
            flags.append(f"{top['ticker']} is {top['weight']}% of portfolio value; concentration is the first risk.")
        top_sector, sector_weight = max(sector_weights.items(), key=lambda item: item[1])
        if sector_weight > 0.42:
            flags.append(f"{top_sector} is {sector_weight * 100:.1f}% of exposure; diversify sector risk.")
        if exposures["beta"] > 1.25:
            flags.append(f"Portfolio beta is {exposures['beta']:.2f}; drawdowns may exceed the market.")
        if exposures["riskScore"] > 58:
            flags.append(f"Weighted risk score is {exposures['riskScore']:.1f}/100; trim high-volatility names first.")
    if not flags:
        flags.append("Portfolio risk is balanced across concentration, factor, and beta checks.")

    return {
        "totalValue": round2(total_value),
        "positions": enriched,
        "sectorWeights": [{"sector": sector, "weight": round2(weight * 100)} for sector, weight in sorted(sector_weights.items())],
        "factorExposures": {key: round2(value) for key, value in exposures.items()},
        "riskFlags": flags,
    }


def valuation_model(ticker: str, assumptions: dict[str, Any] | None = None) -> dict[str, Any]:
    assumptions = assumptions or {}
    company = get_company(ticker)
    if company is None:
        raise ValueError("Unknown ticker")
    growth = float(assumptions.get("revenueGrowth", company["revenueGrowth"] * 100)) / 100
    discount = float(assumptions.get("discountRate", 9.5)) / 100
    terminal_growth = float(assumptions.get("terminalGrowth", 2.8)) / 100
    fcf_margin = float(assumptions.get("fcfMargin", max(company["netMargin"] * 0.72, 3))) / 100
    terminal_multiple = float(assumptions.get("terminalMultiple", company["evEbitda"]))

    forecast = []
    revenue = company["revenue"]
    present_value = 0.0
    for year in range(1, 6):
        revenue *= 1 + growth * (1 - year * 0.045)
        fcf = revenue * fcf_margin
        pv = fcf / ((1 + discount) ** year)
        present_value += pv
        forecast.append({"year": 2026 + year, "revenue": round2(revenue), "freeCashFlow": round2(fcf), "pv": round2(pv)})

    year_five_fcf = forecast[-1]["freeCashFlow"]
    terminal_value = year_five_fcf * (1 + terminal_growth) / max(discount - terminal_growth, 0.015)
    terminal_value_multiple = year_five_fcf * terminal_multiple
    blended_terminal = (terminal_value * 0.65) + (terminal_value_multiple * 0.35)
    pv_terminal = blended_terminal / ((1 + discount) ** 5)
    equity_value = present_value + pv_terminal
    dcf_price = equity_value / company["shares"]

    peer_rows = peers_for(ticker, 5)
    peer_multiple = median([peer["evEbitda"] for peer in peer_rows]) if peer_rows else company["evEbitda"]
    ebitda = company["revenue"] * (company["operatingMargin"] / 100) * 1.18
    comps_price = (ebitda * peer_multiple) / company["shares"]
    book_value_per_share = max(company["eps"] * (100 / max(company["roe"], 1)), 1)
    graham_price = math.sqrt(max(22.5 * company["eps"] * book_value_per_share, 0))
    fair_value = (dcf_price * 0.55) + (comps_price * 0.3) + (graham_price * 0.15)

    sensitivity = []
    for discount_shift in [-1, 0, 1]:
        row = {"discountRate": round2((discount + discount_shift / 100) * 100), "values": []}
        for growth_shift in [-1.5, 0, 1.5]:
            adjusted_growth = growth + growth_shift / 100
            value = _quick_dcf(company, adjusted_growth, discount + discount_shift / 100, terminal_growth, fcf_margin)
            row["values"].append(round2(value))
        sensitivity.append(row)

    return {
        "ticker": company["ticker"],
        "company": company["name"],
        "currentPrice": company["price"],
        "fairValue": round2(fair_value),
        "upsidePct": round2((fair_value / company["price"] - 1) * 100),
        "dcfPrice": round2(dcf_price),
        "compsPrice": round2(comps_price),
        "grahamPrice": round2(graham_price),
        "peerMultiple": round2(peer_multiple),
        "forecast": forecast,
        "sensitivity": sensitivity,
        "assumptions": {
            "revenueGrowth": round2(growth * 100),
            "discountRate": round2(discount * 100),
            "terminalGrowth": round2(terminal_growth * 100),
            "fcfMargin": round2(fcf_margin * 100),
            "terminalMultiple": round2(terminal_multiple),
        },
        "research": research_feed(ticker)[:2],
    }


def _quick_dcf(company: dict[str, Any], growth: float, discount: float, terminal_growth: float, fcf_margin: float) -> float:
    revenue = company["revenue"]
    pv = 0.0
    final_fcf = 0.0
    for year in range(1, 6):
        revenue *= 1 + growth * (1 - year * 0.045)
        final_fcf = revenue * fcf_margin
        pv += final_fcf / ((1 + discount) ** year)
    terminal = final_fcf * (1 + terminal_growth) / max(discount - terminal_growth, 0.015)
    return (pv + terminal / ((1 + discount) ** 5)) / company["shares"]


def market_summary() -> dict[str, Any]:
    company_list = list(companies())
    return {
        "coverage": len(company_list),
        "factorCount": len(company_list[0]["metrics"]) if company_list else 0,
        "avgUpside": round2(mean((company["targetPrice"] / company["price"] - 1) * 100 for company in company_list)),
        "qualityLeaders": sorted(
            [{"ticker": item["ticker"], "name": item["name"], "score": item["qualityScore"]} for item in company_list],
            key=lambda row: row["score"],
            reverse=True,
        )[:5],
        "riskWatch": sorted(
            [{"ticker": item["ticker"], "name": item["name"], "score": item["riskScore"]} for item in company_list],
            key=lambda row: row["score"],
            reverse=True,
        )[:5],
    }
