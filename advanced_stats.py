"""Transparent single-game box-score calculations; no generated code execution."""

import math


def number(value):
    """Missing or invalid inputs remain missing rather than becoming zero."""
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def shooting_inputs(raw):
    result = {}
    for key, made, attempted in (("FG", "FGM", "FGA"), ("3PT", "3PM", "3PA"), ("FT", "FTM", "FTA")):
        parts = str(raw.get(key, "")).split("-")
        values = [number(p) for p in parts] if len(parts) == 2 else [None, None]
        if None not in values and values[0] > values[1]:
            values = [None, None]
        result.update({made: values[0], attempted: values[1]})
    for key in ("PTS", "AST", "TO", "OREB", "DREB", "STL", "BLK", "PF"):
        result[key] = number(raw.get(key))
    return result


def ratio(numerator, denominator, scale=1):
    if denominator <= 0:
        raise ValueError("Denominator is zero or negative; this metric is undefined.")
    return scale * numerator / denominator


# name, label, unit, inputs, formula, estimate flag, calculation
SHOOTING = [
    ("efg_pct", "Effective field goal percentage", "%", ("FGM", "3PM", "FGA"),
     "100 * (FGM + 0.5 * 3PM) / FGA", False,
     lambda s: ratio(s['FGM'] + 0.5 * s['3PM'], s['FGA'], 100)),
    ("ts_pct", "True shooting percentage", "%", ("PTS", "FGA", "FTA"),
     "100 * PTS / (2 * (FGA + 0.44 * FTA))", True,
     lambda s: ratio(s['PTS'], 2 * (s['FGA'] + 0.44 * s['FTA']), 100)),
    ("three_point_attempt_rate", "Three-point attempt share", "%", ("3PA", "FGA"),
     "100 * 3PA / FGA", False, lambda s: ratio(s['3PA'], s['FGA'], 100)),
    ("free_throw_rate", "Free throw attempt rate", "ratio", ("FTA", "FGA"),
     "FTA / FGA", False, lambda s: ratio(s['FTA'], s['FGA'])),
]
PLAYER_METRICS = SHOOTING + [
    ("game_score", "Game Score", "score", ("PTS", "FGM", "FGA", "FTA", "FTM", "OREB", "DREB", "STL", "AST", "BLK", "PF", "TO"),
     "PTS + 0.4*FGM - 0.7*FGA - 0.4*(FTA-FTM) + 0.7*OREB + 0.3*DREB + STL + 0.7*AST + 0.7*BLK - 0.4*PF - TO", False,
     lambda s: s['PTS'] + .4*s['FGM'] - .7*s['FGA'] - .4*(s['FTA']-s['FTM']) + .7*s['OREB'] + .3*s['DREB'] + s['STL'] + .7*s['AST'] + .7*s['BLK'] - .4*s['PF'] - s['TO']),
    ("ast_to_ratio", "Assist-to-turnover ratio", "ratio", ("AST", "TO"),
     "AST / TO", False, lambda s: ratio(s['AST'], s['TO'])),
]
POSSESSION_INPUTS = ("FGA", "FTA", "OREB", "TO", "OPP_FGA", "OPP_FTA", "OPP_OREB", "OPP_TO")
POSSESSION_FORMULA = "0.5 * ((FGA + 0.44*FTA - OREB + TO) + (OPP_FGA + 0.44*OPP_FTA - OPP_OREB + OPP_TO))"


def possessions(s):
    value = .5 * ((s['FGA'] + .44*s['FTA'] - s['OREB'] + s['TO']) +
                  (s['OPP_FGA'] + .44*s['OPP_FTA'] - s['OPP_OREB'] + s['OPP_TO']))
    if value <= 0:
        raise ValueError("Estimated possessions must be positive.")
    return value


TEAM_METRICS = SHOOTING + [
    ("tov_pct", "Turnover percentage", "%", ("TO", "FGA", "FTA"),
     "100 * TO / (FGA + 0.44*FTA + TO)", True,
     lambda s: ratio(s['TO'], s['FGA'] + .44*s['FTA'] + s['TO'], 100)),
    ("oreb_pct", "Offensive rebound percentage", "%", ("OREB", "OPP_DREB"),
     "100 * OREB / (OREB + OPP_DREB)", False,
     lambda s: ratio(s['OREB'], s['OREB'] + s['OPP_DREB'], 100)),
    ("estimated_possessions", "Estimated possessions", "possessions", POSSESSION_INPUTS,
     POSSESSION_FORMULA, True, possessions),
    ("offensive_rating", "Estimated offensive rating", "points per 100 possessions", POSSESSION_INPUTS + ("PTS",),
     "100 * PTS / EST_POSS; EST_POSS = " + POSSESSION_FORMULA, True,
     lambda s: ratio(s['PTS'], possessions(s), 100)),
    ("defensive_rating", "Estimated defensive rating", "points per 100 possessions", POSSESSION_INPUTS + ("OPP_PTS",),
     "100 * OPP_PTS / EST_POSS; EST_POSS = " + POSSESSION_FORMULA, True,
     lambda s: ratio(s['OPP_PTS'], possessions(s), 100)),
    ("net_rating", "Estimated net rating", "points per 100 possessions", POSSESSION_INPUTS + ("PTS", "OPP_PTS"),
     "100 * (PTS - OPP_PTS) / EST_POSS; EST_POSS = " + POSSESSION_FORMULA, True,
     lambda s: ratio(s['PTS'] - s['OPP_PTS'], possessions(s), 100)),
]


def calculate(inputs, definitions, metric="all"):
    aliases = {"efg%": "efg_pct", "ts%": "ts_pct", "gmsc": "game_score",
               "ast/to": "ast_to_ratio", "3par": "three_point_attempt_rate",
               "ftr": "free_throw_rate", "tov%": "tov_pct", "oreb%": "oreb_pct",
               "ortg": "offensive_rating", "drtg": "defensive_rating", "netrtg": "net_rating"}
    key = metric.strip().casefold()
    key = aliases.get(key, key)
    selected = [d for d in definitions if key == "all" or d[0] == key]
    if not selected:
        raise ValueError("Unknown metric. Available: " + ", ".join(d[0] for d in definitions))
    output = {}
    for name, label, unit, required, formula, estimated, fn in selected:
        used = {k: inputs.get(k) for k in required}
        result = {"label": label, "value": None, "unit": unit, "formula": formula,
                  "estimated": estimated, "inputs": used}
        missing = [k for k, v in used.items() if v is None]
        if missing:
            result["unavailable_reason"] = "Missing or invalid inputs: " + ", ".join(missing)
        else:
            try:
                value = fn(used)
                if not math.isfinite(value):
                    raise ValueError("Calculation did not produce a finite value.")
                result["value"] = round(value, 3)
            except ValueError as exc:
                result["unavailable_reason"] = str(exc)
        output[name] = result
    return output
