#!/usr/bin/env python3
import json
import re
from datetime import datetime

import numpy as np
import pyomo.environ as pyo
from flask_login import current_user
from pyomo.environ import (
    ConcreteModel, Set, Param, Var, Binary, NonNegativeReals,
    NonNegativeIntegers, Integers, Objective, Constraint, RangeSet, Reals, Any
)
from pyomo.opt import SolverFactory
import math
from collections import defaultdict

from app.constants import DAYSETUP_TABLE, PHARM_TABLE, DOSING_SCHEMES_TABLE, PATIENTS_TABLE, TEST_PATIENTS_TABLE
from app.table_manager import get_table_manager


# def remove_slash_dash(input_str: str) -> str:
#     """
#     Removes all occurrences of '/' and '-' from the input string.
#
#     Parameters:
#         input_str (str): The string to process.
#
#     Returns:
#         str: A new string with '/' and '-' removed.
#     """
#     # Create a translation table that maps '/' and '-' to None.
#     translation_table = str.maketrans('', '', '/-')
#     return input_str.translate(translation_table)


###############################################################################
# Helper Functions (Keep Existing)
###############################################################################
def compute_ga68_activity(
    initial_activity_GBq,
    days_since_calibration,
    half_life_Ge68_days=270.8,
):
    """
    Compute the equilibrium Ge-68 activity (in MBq) which determines the
    potential Ga-68 generation rate. This represents the 'max_Q' for a standard
    elution time, often normalized or used as a reference.
    For the piecewise function, we directly use single_run_ga68_activity.
    This function helps establish the generator's current state but isn't directly
    used in the piecewise yield calculation itself in this revised approach.
    Let's recalculate max_Q based on a *long* elution to represent the equilibrium MBq.
    """
     # Decay constants (lambda)
    lambda_Ge68 = np.log(2) / half_life_Ge68_days
    # Current Ge-68 activity in MBq (initially in GBq → multiply by 1000 to get MBq)
    current_Ge68_activity_MBq = initial_activity_GBq * 1000.0 * np.exp(-lambda_Ge68 * days_since_calibration)
    # Return the current parent activity; the daughter activity depends on elution time.
    return current_Ge68_activity_MBq # This is the effective equilibrium activity

# Piecewise generation logic is now handled in pre-calculation for Method 5

def initial_activity_load():
    table_manager = get_table_manager()
    # Assume user_id is defined (or another partition key) and table_manager is initialized.
    day_setup = list(table_manager.query_entities(
        DAYSETUP_TABLE,
        query="PartitionKey eq '{}'".format(current_user.username)
    ))[0]

    # Parse generator activity and calibration date
    initial_activity_GBq = float(day_setup['GeneratorActivity'])
    generator_date = datetime.strptime(day_setup['GeneratorDate'], "%Y-%m-%d")

    # Determine elapsed days since calibration (using the current date)
    today = datetime.today()
    elapsed_days_since_calibration = (today - generator_date).days

    return initial_activity_GBq, elapsed_days_since_calibration


def load_pharm():
    table_manager = get_table_manager()
    # Query for all pharmaceutical records
    pharm_entities = table_manager.query_entities(
        PHARM_TABLE,
        query="PartitionKey eq '{}'".format(current_user.username)
    )

    # Prepare dictionaries
    half_life = {}
    cost_per_GBq = {}
    pharma_avail = {}

    for entity in pharm_entities:
        if 'half_life' not in entity:
            continue
        pharma_type = entity['type']
        if pharma_type[:4] == "68Ga":
            pharma_type = "Ga68"
        half_life[pharma_type] = float(entity['half_life'])
        cost_per_GBq[pharma_type] = float(entity['price'])
        # Convert JSON-formatted time_slots into list form
        pharma_avail[pharma_type] = json.loads(entity['time_slots'])

    return half_life, cost_per_GBq, pharma_avail


def load_dosing_schemes():
    table_manager = get_table_manager()
    dosing_schemes = table_manager.query_entities(
        DOSING_SCHEMES_TABLE,
        query="PartitionKey eq '{}'".format(current_user.username)
    )

    # Convert results to a dictionary keyed by the dosing scheme ID.
    dosing_scheme_dict = {}
    for scheme in dosing_schemes:
        scheme_id = scheme['RowKey']
        dosing_scheme_dict[scheme_id] = {
            'Name': scheme['Name'],
            'Radiopharmaceutical': scheme['Radiopharmaceutical'] if scheme['Radiopharmaceutical'][:4] != '68Ga' else "Ga68",
            'DoseValue': scheme['DoseValue'],
            'DoseType': scheme['DoseType'],
            'Uptake1': scheme['Uptake1'],
            'Imaging1': scheme['Imaging1'],
            'Uptake2': scheme['Uptake2'],
            'Imaging2': scheme['Imaging2']
        }

    return dosing_scheme_dict


def load_patient_data(test_id=None):
    table_manager = get_table_manager()
    if test_id:
        # Query for patients associated with a specific test ID
        patients_raw = table_manager.query_entities(
            TEST_PATIENTS_TABLE,
            query="PartitionKey eq '{}' and TestID eq '{}'".format(current_user.username, test_id)
        )
    else:
        patients_raw = table_manager.query_entities(
            PATIENTS_TABLE,
            query="PartitionKey eq '{}'".format(current_user.username)
        )

    # Build a processed list/dictionary of patients
    patients = {}
    for record in patients_raw:
        patient_id = record['RowKey']  # or assign your own identifier
        patients[patient_id] = {
            'Surname': record['Surname'],
            'GivenName': record['GivenName'],
            'Identification': record['Identification'],
            'Weight': record['Weight'],
            'DosingSchemeID': record['DosingSchemeID'],
            'AdministeredDose': record['AdministeredDose']
        }

    return patients


def convert_key(old_key):
    """
    Convert original pharma key to new key.
    - Keys starting with "Ga68" return "Ga68".
    - Keys with pattern like "11C-Cholin" are reformatted to "C11" (swap digit and letter parts).
    - Otherwise, return the original key.
    """
    if old_key.startswith("Ga68"):
        return "Ga68"
    # This regex looks for keys that start with digits followed by a single uppercase letter and a dash or underscore.
    return old_key

def convert_time_str(time_str, base_hour=6):
    """
    Convert a time string in the format "HH:MM" into a step number.
    0 corresponds to base_hour:00 (default 6:00); each step is 5 minutes.
    """
    parts = time_str.split(":")
    hour = int(parts[0])
    minute = int(parts[1])
    # Calculate minutes passed since base_hour:00
    minutes_since_base = (hour * 60 + minute) - (base_hour * 60)
    step = minutes_since_base // 5
    return step

def reformat_pharma_avail(orig_avail, num_steps):
    """
    Reformats the input dictionary of pharmaceutical availabilities according to:
      - Converting keys:
          * If key starts with "Ga68", map to "Ga68" with an empty list.
          * Otherwise, if the key matches the pattern like "11C-...", convert it to "C11".
      - Converting time strings:
          * A time string "HH:MM" is converted into a step number where 0 equals 6:00.
          * The string "anytime" is replaced by list(range(num_steps)).
      - If multiple input keys map to the same new key, their lists are merged (union, sorted).
    """
    new_avail = {}
    for orig_key, times in orig_avail.items():
        new_key = convert_key(orig_key)
        # If the new key is "Ga68", always assign an empty list.
        if new_key == "Ga68":
            new_avail[new_key] = []
            continue

        # If any availability entry is "anytime", then override with full range.
        if any(t == "anytime" for t in times):
            new_val = list(range(num_steps))
        else:
            # Convert each time string to its corresponding step.
            new_val = [convert_time_str(t) for t in times]

        # If the new key already exists (e.g. from merging "11C-Cholin" and "11C-Methionin"),
        # combine the lists using a sorted union.
        if new_key in new_avail:
            # If one of the lists is already the full range (from "anytime"), keep it.
            if new_avail[new_key] == list(range(num_steps)):
                continue
            if new_val == list(range(num_steps)):
                new_avail[new_key] = list(range(num_steps))
            else:
                combined = set(new_avail[new_key]) | set(new_val)
                new_avail[new_key] = sorted(combined)
        else:
            new_avail[new_key] = sorted(new_val)
    return new_avail

###############################################################################
# Main Script
###############################################################################
def cbc(test_id=None):
    # ------------------------------------------------------------------
    # 2. GLOBAL CONSTANTS
    # ------------------------------------------------------------------
    NUM_STEPS = 100  # 50 × 5 min = 250 min horizon
    STEP_MINUTES = 5
    HALF_LIFE_GE68_DAYS = 270.8

    # Generator parameters
    GEN_WARMUP = 0  # warm-up steps
    GEN_COOLDOWN = 30  # cooldown steps
    MAX_ELUTION_STEPS = 10  # max elution duration steps

    # Half-lives
    half_life_Ge68_days = 270.8
    half_life_Ga68_minutes = 67.71

    # ------------------------------------------------------------------
    # 3. DERIVED DATA
    # ------------------------------------------------------------------
    init_GBq, days_elapsed = initial_activity_load()
    current_eq_MBq = A_eq = compute_ga68_activity(init_GBq, days_elapsed, half_life_Ge68_days=HALF_LIFE_GE68_DAYS)

    half_life, cost_per_GBq, pharma_avail = load_pharm()
    dosing_scheme_dict = load_dosing_schemes()
    patients = load_patient_data(test_id)

    # keep only isotopes actually requested today
    pharma_set = set()
    for pdat in patients.values():
        scheme = dosing_scheme_dict[pdat["DosingSchemeID"]]
        pharma = scheme["Radiopharmaceutical"]
        if pharma[:4] == "68Ga":
            pharma = "Ga68"
        pharma_set.add(pharma)

    pharma_avail = reformat_pharma_avail(pharma_avail, num_steps=NUM_STEPS)
    pharma_avail = {k: v for k, v in pharma_avail.items() if k in pharma_set}
    half_life = {k: v for k, v in half_life.items() if k in pharma_set}
    cost_per_GBq = {k: v for k, v in cost_per_GBq.items() if k in pharma_set}

    # --- patient‑indexed dictionaries ---------------------------------
    patient_set, phi, dose_MBq, u1, i1, u2, i2 = [], {}, {}, {}, {}, {}, {}
    patient_identifiers = []
    for idx, (pid, pdat) in enumerate(patients.items(), start=1):
        patient_set.append(idx)
        scheme = dosing_scheme_dict[pdat["DosingSchemeID"]]
        rp = scheme["Radiopharmaceutical"]
        if rp[:4] == "68Ga":
            rp = "Ga68"
        phi[idx] = rp
        dose_MBq[idx] = pdat["AdministeredDose"]
        u1[idx] = int(scheme["Uptake1"]) // STEP_MINUTES
        i1[idx] = int(scheme["Imaging1"]) // STEP_MINUTES
        u2[idx] = int(scheme["Uptake2"]) // STEP_MINUTES
        i2[idx] = int(scheme["Imaging2"]) // STEP_MINUTES
        patient_identifiers.append(pid)

    # Precompute Bateman yields for all (s,d)
    yield_Bateman = {}
    # decay constants per minute
    lambda_parent = math.log(2) / (half_life_Ge68_days * 24 * 60)
    lambda_daughter = math.log(2) / half_life_Ga68_minutes
    for s in range(NUM_STEPS):
        for d in range(1, MAX_ELUTION_STEPS + 1):
            # time minutes
            w = GEN_WARMUP * STEP_MINUTES
            dd = d * STEP_MINUTES
            # Bateman yield at end of elution
            term = A_eq * (lambda_daughter / (lambda_daughter - lambda_parent)) * (
                    math.exp(-lambda_parent * w) - math.exp(-lambda_daughter * (w + dd))
            )
            # assign only if fits
            if s + GEN_WARMUP + d + GEN_COOLDOWN <= NUM_STEPS:
                yield_Bateman[(s, d)] = term

    # --------------------------------------
    # 3. Build Pyomo Model
    # --------------------------------------
    model = ConcreteModel()

    P = patient_set
    F = pharma_set

    # Sets
    # t_model = list(range(NUM_STEPS))
    # d_model = list(range(1, MAX_ELUTION_STEPS + 1))
    model.T = RangeSet(0, NUM_STEPS - 1)
    model.P = Set(initialize=P)
    model.F = Set(initialize=F)
    model.F_other = Set(initialize=[f for f in pharma_set if f != 'Ga68'])
    model.Sd_index = Set(initialize=list(yield_Bateman.keys()), dimen=2)

    # Params
    model.lambda_decay = Param(model.F, initialize={f: 2 ** (-STEP_MINUTES / half_life[f]) for f in F})
    model.cost = Param(model.F_other, initialize=lambda m, f: cost_per_GBq.get(f, 0), within=Reals)
    # model.avail = Param(model.F_other, initialize=lambda m, f: pharma_avail.get(f, []), within=Set(model.T))
    model.phi = Param(model.P, initialize=phi)
    model.dose = Param(model.P, initialize=dose_MBq)
    model.u1 = Param(model.P, initialize=u1)
    model.i1 = Param(model.P, initialize=i1)
    model.u2 = Param(model.P, initialize=u2)
    model.i2 = Param(model.P, initialize=i2)
    model.Aeq = Param(initialize=A_eq)
    model.lambda_p = Param(initialize=lambda_parent)
    model.lambda_d = Param(initialize=lambda_daughter)

    # Decision Variables
    model.S = Var(model.P, model.T, domain=Binary)
    model.x = Var(model.F_other, model.T, domain=NonNegativeReals)
    model.I = Var(model.F, model.T, domain=NonNegativeReals)
    model.Sg = Var(model.Sd_index, domain=Binary)
    model.G = Var(model.T, domain=NonNegativeReals)

    # Objective: minimize purchase cost
    def obj_rule(m):
        return sum(m.cost[f] * m.x[f, t] / 1000.0 for f in m.F_other for t in m.T)

    model.Obj = Objective(rule=obj_rule)

    # 1. Each patient starts once
    def one_start(m, p):
        return sum(m.S[p, t] for t in m.T) == 1

    model.C_one_start = Constraint(model.P, rule=one_start)

    # 2. Time horizon
    def horizon(m, p, t):
        end = t + m.u1[p] + m.i1[p] + m.u2[p] + m.i2[p]
        if end > NUM_STEPS:
            return m.S[p, t] == 0
        return Constraint.Skip

    model.C_horizon = Constraint(model.P, model.T, rule=horizon)

    # 3. Scanner non-overlap
    def no_overlap(m, p1, p2, t1, t2):
        if p1 >= p2: return Constraint.Skip
        # intervals
        p1s1 = t1 + m.u1[p1]
        p1e1 = p1s1 + m.i1[p1]
        p1s2 = t1 + m.u1[p1] + m.i1[p1] + m.u2[p1]
        p1e2 = p1s2 + m.i2[p1]
        p2s1 = t2 + m.u1[p2]
        p2e1 = p2s1 + m.i1[p2]
        p2s2 = t2 + m.u1[p2] + m.i1[p2] + m.u2[p2]
        p2e2 = p2s2 + m.i2[p2]
        overlap = False
        if m.i1[p1] > 0 and m.i1[p2] > 0 and max(p1s1, p2s1) < min(p1e1, p2e1): overlap = True
        if m.i1[p1] > 0 and m.i2[p2] > 0 and max(p1s1, p2s2) < min(p1e1, p2e2): overlap = True
        if m.i2[p1] > 0 and m.i1[p2] > 0 and max(p1s2, p2s1) < min(p1e2, p2e1): overlap = True
        if m.i2[p1] > 0 and m.i2[p2] > 0 and max(p1s2, p2s2) < min(p1e2, p2e2): overlap = True
        if overlap:
            return m.S[p1, t1] + m.S[p2, t2] <= 1
        return Constraint.Skip

    model.C_no_overlap = Constraint(model.P, model.P, model.T, model.T, rule=no_overlap)

    def compute_demand(m, f, t):
        demand = 0
        for p in m.P:
            if m.phi[p] == f:
                if m.u1[p] > 0:
                    demand += m.dose[p] * m.S[p, t]
                if m.u2[p] > 0 and t - m.u1[p] - m.i1[p] >= 0:
                    demand += m.dose[p] * m.S[p, t - m.u1[p] - m.i1[p]]
                if m.u1[p] == 0 and m.u2[p] == 0:
                    demand += m.dose[p] * m.S[p, t]
        return demand

    # 4. Inventory balance for other isotopes
    def inv_other(m, f, t):
        demand = compute_demand(m, f, t)
        if t == 0:
            return m.I[f, 0] == m.x[f, 0] - demand
        return m.I[f, t] == m.I[f, t - 1] * m.lambda_decay[f] + m.x[f, t] - demand

    model.C_inv_other = Constraint(model.F_other, model.T, rule=inv_other)

    # 5. Inventory balance for Ga68
    def inv_ga68(m, t):
        f = 'Ga68'
        # demand = sum(m.dose[p] * m.S[p, t] for p in m.P if m.phi[p] == f)
        demand = compute_demand(m, f, t)
        if t == 0:
            return m.I[f, 0] == m.G[0] - demand
        return m.I[f, t] == m.I[f, t - 1] * m.lambda_decay[f] + m.G[t] - demand

    if 'Ga68' in pharma_set:
        model.C_inv_ga68 = Constraint(model.T, rule=inv_ga68)

    # 6. Sufficient inventory
    def sufficient(m, f, t):
        demand = compute_demand(m, f, t)
        # demand = sum(m.dose[p] * m.S[p, t] for p in m.P if m.phi[p] == f)
        inv_start = m.I[f, t - 1] * m.lambda_decay[f] if t > 0 else 0
        prod = m.G[t] if f == 'Ga68' else m.x[f, t]
        return inv_start + prod >= demand

    model.C_sufficient = Constraint(model.F, model.T, rule=sufficient)

    # 7. Purchase availability windows
    def avail(m, f, t):
        if t not in pharma_avail.get(f, []):
            return m.x[f, t] == 0
        return Constraint.Skip

    model.C_avail = Constraint(model.F_other, model.T, rule=avail)

    # 8. Generator non-overlap

    def gen_nonoverlap(m, t):
        return sum(m.Sg[s, d] for (s, d) in m.Sd_index if s <= t < s + GEN_WARMUP + d + GEN_COOLDOWN) <= 1

    model.C_gen_nonoverlap = Constraint(model.T, rule=gen_nonoverlap)

    # 9. Link G to runs

    def link_G(m, t):
        return m.G[t] == sum(m.Sg[s, d] * yield_Bateman[(s, d)]
                             for (s, d) in m.Sd_index
                             if s + GEN_WARMUP + d == t)

    model.C_link_G = Constraint(model.T, rule=link_G)

    # --------------------------------------
    # 4. Solve
    # --------------------------------------
    solver = SolverFactory('cbc')
    results = solver.solve(model, tee=True)

    # --------------------------------------
    # 5. Reporting
    # --------------------------------------
    print("\n=== Patient Schedules ===")
    schedules = {}
    for p in model.P:
        for t in model.T:
            if pyo.value(model.S[p, t]) > 0.5:
                schedules[patient_identifiers[p - 1]] = t * STEP_MINUTES
                print(f"Patient {p} starts at step {t} (min {t * STEP_MINUTES})")

    print("\n=== Purchases ===")
    for f in model.F_other:
        for t in model.T:
            if pyo.value(model.x[f, t]) > 1e-6:
                print(f"Buy {pyo.value(model.x[f, t]):.1f} MBq of {f} at step {t} (min {t * STEP_MINUTES})")

    ga68_generator = []
    print("\n=== Ga-68 Generator Runs ===")
    for (s, d) in model.Sd_index:
        if pyo.value(model.Sg[s, d]) > 0.5:
            ga68_generator.append(
                (s * STEP_MINUTES, (s + d) * STEP_MINUTES))
            print(
                f"Start warm-up at step {s} (min {s * STEP_MINUTES}), elute for {d * STEP_MINUTES} mins, cooldown {GEN_COOLDOWN * STEP_MINUTES} mins")

    print("\n=== Inventory End Levels ===")
    pharma_inventory = []
    for f in model.F:
        levels = [pyo.value(model.I[f, t]) for t in model.T]
        if f == 'Ga68':
            levels = []
            purchases = []
            for t in model.T:
                inv_start = pyo.value(model.I[f, t - 1] * model.lambda_decay[f]) if t > 0 else 0
                prod = pyo.value(model.G[t])
                levels.append(inv_start + prod)
                purchases.append(0)
        else:
            purchases = [pyo.value(model.x[f, t]) for t in model.T]
        pharma_inventory.append((f, purchases, levels))
        print(f"Final inventory of {f}: {levels[-1]:.1f} MBq")

    print("\nDone.")

    print("\n============================\n")

    return schedules, ga68_generator, pharma_inventory
    # return None

# Example run command: python your_script_name.py

if __name__ == '__main__':
    cbc()