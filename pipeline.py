from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


MANIFEST_COLUMNS = ["episode_id", "create_time", "avg_score", "min_score", "sum_score", "agent_count", "size_bytes"]
SHARED_FIELDS = ("day", "hour", "step", "farms", "market", "town")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def source_date(path: Path, frame: pd.DataFrame) -> pd.Series:
    if "crawl_date" in frame:
        return frame["crawl_date"].astype("string")
    parsed = pd.to_datetime(frame.get("create_time"), errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d")


def load_manifests(paths: Iterable[Path]) -> pd.DataFrame:
    parts = []
    for path in paths:
        frame = pd.read_csv(path)
        missing = set(MANIFEST_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        frame = frame.copy()
        frame["episode_id"] = frame["episode_id"].astype("string")
        frame["create_time"] = pd.to_datetime(frame["create_time"], errors="coerce")
        frame["source_manifest"] = str(path)
        frame["crawl_date"] = source_date(path, frame)
        parts.append(frame)
    if not parts:
        return pd.DataFrame(columns=MANIFEST_COLUMNS)
    return pd.concat(parts, ignore_index=True, sort=False)


def deduplicate_manifests(manifests: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if manifests.empty:
        return manifests.copy(), pd.DataFrame()
    grouped = manifests.groupby("episode_id", dropna=False)
    history = grouped.agg(
        first_seen_date=("crawl_date", "min"),
        last_seen_date=("crawl_date", "max"),
        manifest_occurrences=("episode_id", "size"),
        source_count=("source_manifest", "nunique"),
    ).reset_index()
    ordered = manifests.sort_values(["episode_id", "create_time", "source_manifest"], na_position="first")
    representatives = ordered.drop_duplicates("episode_id", keep="last").merge(history, on="episode_id", how="left")
    duplicate_trace = manifests.merge(representatives[["episode_id", "source_manifest", "create_time"]], on="episode_id", how="left", suffixes=("", "_kept"))
    duplicate_trace["is_representative"] = (duplicate_trace["source_manifest"] == duplicate_trace["source_manifest_kept"]) & (duplicate_trace["create_time"] == duplicate_trace["create_time_kept"])
    duplicate_trace["dedup_reason"] = np.where(duplicate_trace["is_representative"], "kept_latest_create_time", "duplicate_episode_id_removed")
    return representatives.reset_index(drop=True), duplicate_trace.drop(columns=["source_manifest_kept", "create_time_kept"])


def tile_features(tiles: Any) -> dict[str, int]:
    counts = {"empty_tiles": 0, "locked_tiles": 0, "plant_tiles": 0, "pasture_tiles": 0, "weed_tiles": 0, "unknown_tiles": 0}
    for row in tiles if isinstance(tiles, list) else []:
        for tile in row if isinstance(row, list) else []:
            if tile is None:
                counts["empty_tiles"] += 1
            elif tile == "LOCKED":
                counts["locked_tiles"] += 1
            elif isinstance(tile, dict):
                counts[{"PLANT": "plant_tiles", "PASTURE": "pasture_tiles", "WEED": "weed_tiles"}.get(tile.get("kind"), "unknown_tiles")] += 1
            else:
                counts["unknown_tiles"] += 1
    return counts


def parse_episode(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    info = data.get("info") or {}
    config = data.get("configuration") or {}
    episode_id = str(info.get("EpisodeId"))
    rewards = data.get("rewards") or []
    statuses = data.get("statuses") or []
    steps = data.get("steps") or []
    specification = data.get("specification") or {}
    frames = [frame for step_frames in steps if isinstance(step_frames, list) for frame in step_frames if isinstance(frame, dict)]
    turns, orders, shared_checks = [], [], []
    for step_no, step_frames in enumerate(steps):
        by_player = {}
        for player, frame in enumerate(step_frames if isinstance(step_frames, list) else []):
            by_player[player] = frame
            obs = frame.get("observation") or {}
            action = frame.get("action") or {}
            farms = obs.get("farms") or []
            farm = farms[player] if player < len(farms) and isinstance(farms[player], dict) else {}
            market = action.get("market") if isinstance(action.get("market"), list) else []
            reward = frame.get("reward")
            turns.append({
                "episode_id": episode_id, "replay_id": data.get("id"), "source_json": path.name,
                "module_version": data.get("module_version"), "step": step_no, "player": player,
                "status": frame.get("status"), "reward": reward, "reward_is_null": reward is None,
                "money_over_time": farm.get("money"), "market_order_count": len(market),
                "is_default_pass": action == {"farmer": ["PASS"], "hands": [], "market": []},
                **tile_features(farm.get("tiles")),
            })
            for order_index, order in enumerate(market):
                quantity = order[2] if isinstance(order, list) and len(order) > 2 else None
                numeric = isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
                orders.append({"episode_id": episode_id, "step": step_no, "player": player, "order_index": order_index,
                               "operation": order[0] if isinstance(order, list) and order else None,
                               "item": order[1] if isinstance(order, list) and len(order) > 1 else None,
                               "quantity": quantity, "quantity_is_negative": numeric and quantity < 0,
                               "quantity_is_numeric": numeric, "raw_order_json": canonical(order)})
        if 0 in by_player and 1 in by_player:
            left, right = (by_player[p].get("observation") or {} for p in (0, 1))
            check = {"episode_id": episode_id, "step": step_no}
            for field in SHARED_FIELDS:
                check[f"{field}_mismatch"] = left.get(field) != right.get(field)
            check["shared_observation_mismatch"] = any(check[f"{field}_mismatch"] for field in SHARED_FIELDS)
            shared_checks.append(check)
    first = steps[0] if steps and isinstance(steps[0], list) else []
    agent_count = len(first) or len(rewards)
    spec_signature = canonical(specification)
    episode = {
        "episode_id": episode_id, "filename_id_match": path.stem == episode_id,
        "replay_id": data.get("id"), "module_version": data.get("module_version"),
        "schema_signature": spec_signature, "schema_signature_hash": hashlib.sha256(spec_signature.encode("utf-8")).hexdigest(),
        "configured_episode_steps": config.get("episodeSteps"), "actual_steps": len(steps),
        "agent_count_json": agent_count, "json_reward_count": len(rewards), "json_status_count": len(statuses),
        "json_rewards": canonical(rewards), "json_statuses": canonical(statuses),
        "json_reward_sum": sum(x for x in rewards if isinstance(x, (int, float))),
        "final_statuses": canonical([next((f[p].get("status") for f in reversed(steps) if isinstance(f, list) and p < len(f) and isinstance(f[p], dict)), None) for p in range(agent_count)]),
        "schema_version": data.get("schema_version"),
    }
    return episode, turns, orders, shared_checks


def merge_json(episode_dir: Path, episode_ids: set[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    episodes, turns, orders, shared = [], [], [], []
    selected_paths = [path for path in sorted(episode_dir.glob("*.json")) if episode_ids is None or path.stem in episode_ids]
    total = len(selected_paths)
    for index, path in enumerate(selected_paths, start=1):
        if path.name == "manifest.json" or (episode_ids is not None and path.stem not in episode_ids):
            continue
        try:
            episode, turn_rows, order_rows, shared_rows = parse_episode(path)
            episodes.append(episode); turns.extend(turn_rows); orders.extend(order_rows); shared.extend(shared_rows)
            if index == 1 or index % 25 == 0 or index == total:
                print(f"parsed {index}/{total}: {path.name}", flush=True)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            episodes.append({"episode_id": path.stem, "parse_error": str(exc), "filename_id_match": False})
    return pd.DataFrame(episodes), pd.DataFrame(turns), pd.DataFrame(orders), pd.DataFrame(shared)


def add_rule_flags(manifest: pd.DataFrame, episodes: pd.DataFrame, turns: pd.DataFrame, orders: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ep = manifest.merge(episodes, on="episode_id", how="outer", suffixes=("", "_json"))
    ep["json_available"] = ep["actual_steps"].notna()
    ep["rule_score_relation"] = (ep["sum_score"] - ep["avg_score"] * ep["agent_count"]).abs() > 0.1
    ep["rule_min_above_avg"] = ep["min_score"] > ep["avg_score"]
    ep["rule_reward_sum_mismatch"] = (ep["json_reward_sum"] - ep["sum_score"]).abs() > 0.1
    ep["rule_agent_count_mismatch"] = ep["agent_count"] != ep["agent_count_json"]
    ep["rule_status_count_mismatch"] = ep["agent_count"] != ep["json_status_count"]
    ep["rule_steps_mismatch"] = ep["actual_steps"] != ep["configured_episode_steps"]
    ep["rule_filename_id_mismatch"] = ep["json_available"] & ep["filename_id_match"].fillna(False).eq(False)
    rule_columns = ["rule_score_relation", "rule_min_above_avg", "rule_reward_sum_mismatch", "rule_agent_count_mismatch", "rule_status_count_mismatch", "rule_steps_mismatch", "rule_filename_id_mismatch"]
    ep["rule_any_episode_anomaly"] = ep[rule_columns].fillna(False).any(axis=1)
    if not turns.empty:
        turn_flags = turns.groupby("episode_id").agg(
            rule_reward_null=("reward_is_null", "any"),
            rule_abnormal_status=("status", lambda x: x.isin(["ERROR", "TIMEOUT"]).any()),
        ).reset_index()
        turns = turns.merge(turn_flags, on="episode_id", how="left")
    if not orders.empty:
        order_flags = orders.groupby("episode_id")["quantity_is_negative"].any().rename("rule_negative_quantity").reset_index()
        ep = ep.merge(order_flags, on="episode_id", how="left")
    ep["rule_negative_quantity"] = ep.get("rule_negative_quantity", pd.Series(False, index=ep.index)).fillna(False)
    ep["rule_any_episode_anomaly"] = ep["rule_any_episode_anomaly"] | ep["rule_negative_quantity"]
    ep["exclude_from_clean_analysis"] = (~ep["json_available"]) | ep[["rule_agent_count_mismatch", "rule_status_count_mismatch", "rule_steps_mismatch", "rule_filename_id_mismatch", "rule_negative_quantity"]].fillna(False).any(axis=1)
    return ep, turns, orders


def add_statistical_flags(ep: pd.DataFrame) -> pd.DataFrame:
    ep = ep.copy()
    for column, flag in [("avg_score", "stat_avg_score_outlier"), ("size_bytes", "stat_size_outlier"), ("actual_steps", "stat_steps_outlier")]:
        if column not in ep:
            ep[flag] = False
            continue
        low, high = ep[column].quantile([0.01, 0.99])
        ep[flag] = (ep[column] < low) | (ep[column] > high)
    ep["stat_any_outlier"] = ep[[c for c in ep.columns if c.startswith("stat_") and c != "stat_any_outlier"]].any(axis=1)
    ep["rule_only"] = ep["rule_any_episode_anomaly"] & ~ep["stat_any_outlier"]
    ep["stat_only"] = ep["stat_any_outlier"] & ~ep["rule_any_episode_anomaly"]
    ep["both_rule_and_stat"] = ep["stat_any_outlier"] & ep["rule_any_episode_anomaly"]
    return ep


def make_traceability(ep: pd.DataFrame, duplicate_trace: pd.DataFrame) -> pd.DataFrame:
    records = []
    for _, row in ep.iterrows():
        for column in [c for c in ep.columns if c.startswith("rule_") or c.startswith("stat_")]:
            if bool(row.get(column, False)):
                records.append({"episode_id": row.get("episode_id"), "record_level": "episode", "flag": column, "action": "flag_and_exclude_from_clean_analysis", "reversible": True})
    if not duplicate_trace.empty:
        for _, row in duplicate_trace[~duplicate_trace["is_representative"]].iterrows():
            records.append({"episode_id": row["episode_id"], "record_level": "manifest", "flag": "duplicate_episode_id", "action": "deduplicate_keep_latest_create_time", "reversible": True, "source_manifest": row["source_manifest"]})
    return pd.DataFrame(records)


def run_pipeline(
    data_dir: Path,
    manifest_paths: Iterable[Path] | Path | None = None,
    output_dir: Path | None = None,
    max_episodes: int | None = 300,
) -> dict[str, pd.DataFrame]:
    if output_dir is None:
        output_dir = Path(__file__).resolve().parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    if manifest_paths is None:
        manifest_paths = [data_dir / "manifest.csv"]
    elif isinstance(manifest_paths, Path):
        manifest_paths = [manifest_paths]

    raw_manifests = load_manifests(manifest_paths)
    manifests, duplicate_trace = deduplicate_manifests(raw_manifests)
    episode_ids = set(manifests["episode_id"].astype(str))
    if max_episodes is not None:
        episode_ids = set(sorted(episode_ids)[:max_episodes])
    episodes, turns, orders, shared = merge_json(data_dir, episode_ids)
    episode_audit, turns, orders = add_rule_flags(manifests, episodes, turns, orders)
    episode_audit = add_statistical_flags(episode_audit)
    trace = make_traceability(episode_audit, duplicate_trace)
    clean_episode = episode_audit.loc[~episode_audit["exclude_from_clean_analysis"].fillna(False)].copy()
    available_manifest = manifests[manifests["episode_id"].isin(episode_audit.loc[episode_audit["json_available"], "episode_id"])]
    raw_rank = available_manifest.sort_values("avg_score", ascending=False)[["episode_id", "avg_score"]].assign(rank_raw=lambda x: x["avg_score"].rank(method="min", ascending=False))
    clean_rank = clean_episode.sort_values("avg_score", ascending=False)[["episode_id", "avg_score"]].assign(rank_clean=lambda x: x["avg_score"].rank(method="min", ascending=False))
    ranking_impact = raw_rank.merge(clean_rank, on="episode_id", how="outer", suffixes=("_raw", "_clean"))
    ranking_impact["rank_changed"] = ranking_impact["rank_raw"].ne(ranking_impact["rank_clean"])
    record_counts = pd.DataFrame({"stage": ["raw_manifest_rows", "deduplicated_manifest_rows", "json_episode_rows", "turn_rows", "market_order_rows", "clean_episode_rows", "traceability_rows"], "count": [len(raw_manifests), len(manifests), len(episodes), len(turns), len(orders), len(clean_episode), len(trace)]})
    outputs = {"manifests": manifests, "duplicate_trace": duplicate_trace, "episodes": episode_audit, "turns": turns, "market_orders": orders, "shared_checks": shared, "traceability": trace, "ranking_impact": ranking_impact, "record_counts": record_counts}
    for name, frame in outputs.items():
        frame.to_csv(output_dir / f"{name}.csv", index=False)
        if name in {"turns", "market_orders", "episodes"}:
            try:
                frame.to_parquet(output_dir / f"{name}.parquet", index=False)
            except Exception as e:
                pass
    return outputs


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    data_dir = root.parent / "data"
    output = run_pipeline(data_dir, [data_dir / "manifest.csv"], root / "output", max_episodes=300)
    print({name: frame.shape for name, frame in output.items()})
