#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

os.environ.setdefault("ENABLE_SCHEDULER", "0")
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from app import (
        app,
        _get_recommended_strategy,
        get_local_recommendations,
        _temporary_backtest_cutoff_period,
    )
    from models import BacktestRun, LotteryDraw, db
except ModuleNotFoundError as exc:
    missing = getattr(exc, "name", "") or str(exc)
    print(
        json.dumps(
            {
                "error": f"Missing dependency: {missing}",
                "message": "Run this script inside the project environment after installing Flask and the app dependencies.",
                "example": "pip install flask flask-sqlalchemy requests apscheduler",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(1)


DEFAULT_STRATEGIES = ["ml", "hybrid", "balanced", "markov", "trend", "hot", "cold"]


def _normalize_draw(record):
    if hasattr(record, "to_dict"):
        return record.to_dict()
    return record


def _load_region_draws(region):
    records = (
        LotteryDraw.query.filter_by(region=region)
        .order_by(LotteryDraw.draw_date.asc(), LotteryDraw.draw_id.asc())
        .all()
    )
    return [_normalize_draw(record) for record in records]


def _safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _evaluate_prediction(result, draw):
    actual_special = str(draw.get("sno") or "").strip()
    actual_zodiac = str(draw.get("sno_zodiac") or "").strip()
    predicted_special = str((result.get("special") or {}).get("number") or "").strip()
    predicted_zodiac = str((result.get("special") or {}).get("sno_zodiac") or "").strip()
    normal_numbers = [str(num) for num in (result.get("normal") or [])]

    exact_hit = bool(actual_special and predicted_special == actual_special)
    top6_hit = actual_special in normal_numbers if actual_special else False
    zodiac_hit = bool(actual_zodiac and predicted_zodiac and actual_zodiac == predicted_zodiac)
    return {
        "exact_hit": exact_hit,
        "top6_hit": top6_hit,
        "zodiac_hit": zodiac_hit,
        "predicted_special": predicted_special,
        "actual_special": actual_special,
    }


def _window_summary(entries, limit):
    sample = entries[-limit:] if limit and len(entries) > limit else list(entries)
    total = len(sample)
    if total <= 0:
        return {"window": limit, "total": 0, "top1_hit_rate": 0.0, "top6_hit_rate": 0.0, "zodiac_hit_rate": 0.0}

    top1 = sum(1 for item in sample if item["exact_hit"])
    top6 = sum(1 for item in sample if item["top6_hit"])
    zodiac = sum(1 for item in sample if item["zodiac_hit"])
    return {
        "window": limit,
        "total": total,
        "top1_hit_rate": round(top1 / total * 100, 2),
        "top6_hit_rate": round(top6 / total * 100, 2),
        "zodiac_hit_rate": round(zodiac / total * 100, 2),
    }


def _summarize_strategy(entries):
    total = len(entries)
    if total <= 0:
        return {
            "total": 0,
            "top1_hit_rate": 0.0,
            "top6_hit_rate": 0.0,
            "zodiac_hit_rate": 0.0,
            "windows": [],
        }

    top1 = sum(1 for item in entries if item["exact_hit"])
    top6 = sum(1 for item in entries if item["top6_hit"])
    zodiac = sum(1 for item in entries if item["zodiac_hit"])
    return {
        "total": total,
        "top1_hit_rate": round(top1 / total * 100, 2),
        "top6_hit_rate": round(top6 / total * 100, 2),
        "zodiac_hit_rate": round(zodiac / total * 100, 2),
        "windows": [
            _window_summary(entries, 20),
            _window_summary(entries, 50),
            _window_summary(entries, 100),
        ],
    }


def _resolve_strategy(strategy, history_desc, region):
    return strategy


def run_backtest(region, strategies, min_history=60, limit=None):
    draws = _load_region_draws(region)
    if limit and limit > 0:
        draws = draws[-limit:]

    strategy_logs = defaultdict(list)
    detailed_rows = []
    effective_min_history = min(max(1, int(min_history or 1)), max(1, len(draws) - 1))
    if len(draws) <= 1:
        return {
            "region": region,
            "strategies": strategies,
            "periods_evaluated": 0,
            "strategy_results": {},
            "details": [],
        }

    for idx in range(effective_min_history, len(draws)):
        target_draw = draws[idx]
        history_desc = list(reversed(draws[:idx]))
        period = str(target_draw.get("id") or "")
        for strategy in strategies:
            resolved_strategy = _resolve_strategy(strategy, history_desc, region)
            try:
                with _temporary_backtest_cutoff_period(period):
                    result = get_local_recommendations(resolved_strategy, history_desc, region)
            except Exception as exc:
                strategy_logs[strategy].append({
                    "period": period,
                    "error": str(exc),
                    "exact_hit": False,
                    "top6_hit": False,
                    "zodiac_hit": False,
                })
                continue

            evaluation = _evaluate_prediction(result, target_draw)
            row = {
                "period": period,
                "strategy": strategy,
                "resolved_strategy": resolved_strategy,
                "predicted_special": evaluation["predicted_special"],
                "actual_special": evaluation["actual_special"],
                "exact_hit": evaluation["exact_hit"],
                "top6_hit": evaluation["top6_hit"],
                "zodiac_hit": evaluation["zodiac_hit"],
            }
            strategy_logs[strategy].append(row)
            detailed_rows.append(row)

    strategy_results = {
        strategy: _summarize_strategy(entries)
        for strategy, entries in strategy_logs.items()
    }
    ranking = sorted(
        [
            {
                "strategy": strategy,
                "top1_hit_rate": summary["top1_hit_rate"],
                "top6_hit_rate": summary["top6_hit_rate"],
                "zodiac_hit_rate": summary["zodiac_hit_rate"],
                "composite_score": round(
                    summary["top1_hit_rate"] +
                    summary["top6_hit_rate"] * 0.35 +
                    summary["zodiac_hit_rate"] * 0.15,
                    4,
                ),
                "total": summary["total"],
            }
            for strategy, summary in strategy_results.items()
        ],
        key=lambda item: (item["top1_hit_rate"], item["top6_hit_rate"], item["total"]),
        reverse=True,
    )
    return {
        "region": region,
        "strategies": strategies,
        "periods_evaluated": max(0, len(draws) - effective_min_history),
        "strategy_results": strategy_results,
        "ranking": ranking,
        "details": detailed_rows,
    }


def persist_backtest(name, region, strategies, payload):
    record = BacktestRun(
        name=name,
        region=region,
        strategies=",".join(strategies),
        periods_evaluated=int(payload.get("periods_evaluated") or 0),
        payload=json.dumps(payload, ensure_ascii=False),
    )
    db.session.add(record)
    db.session.commit()
    return record


def write_output_file(output_dir, payload):
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    region = payload.get("region") or "all"
    path = os.path.join(output_dir, f"backtest_{region}_{timestamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def parse_args():
    parser = argparse.ArgumentParser(description="Offline backtest for prediction strategies")
    parser.add_argument("--region", choices=["hk", "macau"], default="macau")
    parser.add_argument("--strategies", nargs="+", default=DEFAULT_STRATEGIES)
    parser.add_argument("--min-history", type=int, default=60)
    parser.add_argument("--limit", type=int, default=0, help="Use only the latest N draws")
    parser.add_argument("--name", default="", help="Optional backtest run name")
    parser.add_argument("--output-dir", default=os.path.join("data", "backtests"))
    return parser.parse_args()


def main():
    args = parse_args()
    with app.app_context():
        payload = run_backtest(
            region=args.region,
            strategies=args.strategies,
            min_history=max(12, args.min_history),
            limit=args.limit if args.limit > 0 else None,
        )
        payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        run_name = args.name or f"{args.region}-{'-'.join(args.strategies)}"
        record = persist_backtest(run_name, args.region, args.strategies, payload)
        output_path = write_output_file(args.output_dir, payload)
        print(json.dumps({
            "backtest_run_id": record.id,
            "output_path": output_path,
            "ranking": payload.get("ranking", []),
            "periods_evaluated": payload.get("periods_evaluated", 0),
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
