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

from app.constants import DAYSETUP_TABLE, PHARM_TABLE, DOSING_SCHEMES_TABLE, PATIENTS_TABLE
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

def single_run_ga68_activity(
    equilibrium_MBq, # Current Ge-68 activity in MBq
    run_steps,       # Duration of the elution in steps
    step_minutes=5,
    half_life_Ga68_minutes=67.71
):
    """
    Returns the Ga-68 (MBq) extracted in a single generator run of 'run_steps'
    time-steps (each step being 'step_minutes' long).
    Uses the usual formula:  A_Ge * (1 - exp(-lambda_Ga * t)).
    """
    lambda_Ga68 = np.log(2) / half_life_Ga68_minutes
    total_minutes = run_steps * step_minutes
    # The extracted portion is given by (1 - exp(-lambda * total_minutes)).
    extracted = equilibrium_MBq * (1 - np.exp(-lambda_Ga68 * total_minutes))
    # Minimum yield could be considered non-zero even for 0 steps if there's residual
    # For simplicity, assume 0 steps yield 0.
    if run_steps == 0:
        return 0.0
    return extracted

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


def load_patient_data():
    table_manager = get_table_manager()
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
def cbc():
    ###########################################################################
    # 1. Define Data and Parameters
    ###########################################################################
    # -----------------------
    # Time Horizon
    # -----------------------
    NUM_STEPS = 100 # 50 * 5 = 250 minutes
    STEP_MINUTES = 5 # Duration of each time step

    # -----------------------
    # Ga-68 Generator Parameters
    # -----------------------
    GEN_WARMUP   = 0  # Steps required for warm-up (e.g., 5 mins)
    GEN_COOLDOWN = 30  # Steps required for cool-down (e.g., 5 mins)
    # Half-lives:
    half_life_Ge68_days    = 270.8
    half_life_Ga68_minutes = 67.71

    # Define the maximum practical run duration for Ga-68 generation in steps
    # e.g., cannot run longer than half the total time horizon
    # Or a practical limit like 30 minutes (6 steps)
    # Let's use a reasonable limit, e.g., 10 steps = 50 minutes run time
    MAX_GEN_RUN_STEPS = 10 # Max practical duration of the elution itself

    # Generator initial state
    # initial_activity_GBq = 1.85
    # elapsed_days_since_calibration = 30
    initial_activity_GBq, elapsed_days_since_calibration = initial_activity_load()

    # Calculate the current effective equilibrium activity of the generator
    # This is the parent activity that drives Ga-68 production
    current_equilibrium_MBq = compute_ga68_activity(
        initial_activity_GBq,
        elapsed_days_since_calibration,
        half_life_Ge68_days=half_life_Ge68_days,
    )

    half_life, cost_per_GBq, pharma_avail = load_pharm()
    print(half_life)
    print(cost_per_GBq)
    print(pharma_avail)
    dosing_scheme_dict = load_dosing_schemes()
    print(dosing_scheme_dict)
    patients = load_patient_data()
    print(patients)
    # Initialize an empty set to store radiopharmaceutical names
    pharma_set = set()
    # Iterate over each patient record in the "pacients" dictionary
    for patient_id, patient_data in patients.items():
        dosing_scheme_id = patient_data.get("DosingSchemeID")
        if dosing_scheme_id in dosing_scheme_dict:
            # Get the Radiopharmaceutical used in the dosing scheme
            pharma = dosing_scheme_dict[dosing_scheme_id].get("Radiopharmaceutical")
            if pharma[:4] == "68Ga":
                pharma = "Ga68"
            pharma_set.add(pharma)
        else:
            print(f"Warning: DosingSchemeID {dosing_scheme_id} not found for patient {patient_id}")
    pharma_set = list(pharma_set)
    print(pharma_set)

    pharma_avail = reformat_pharma_avail(pharma_avail, num_steps=NUM_STEPS)
    # only keep the ones that are in pharma_set
    pharma_avail = {k: v for k, v in pharma_avail.items() if k in pharma_set}
    half_life = {k: v for k, v in half_life.items() if k in pharma_set}
    cost_per_GBq = {k: v for k, v in cost_per_GBq.items() if k in pharma_set}

    print(pharma_avail)
    print(half_life)
    print(cost_per_GBq)
    # pharma_set = ['Ga68','F18','C11','O15','N13']

    # Initialize the output variables.
    patient_set = []  # List of patient identifiers (e.g. 1, 2, 3)
    phi = {}  # Maps patient id -> Radiopharmaceutical (from the dosing scheme)
    dose_MBq = {}  # Maps patient id -> AdministeredDose (from the patient record)
    u1 = {}  # Maps patient id -> Uptake1 (from the dosing scheme)
    i1 = {}  # Maps patient id -> Imaging1 (from the dosing scheme)
    u2 = {}  # Maps patient id -> Uptake2 (from the dosing scheme)
    i2 = {}  # Maps patient id -> Imaging2 (from the dosing scheme)

    # Assign sequential identifiers (1,2,3, …) in any order.
    # Here we iterate over the items in the patients dictionary.
    patient_identifiers = []
    for idx, (pat_key, pat_data) in enumerate(patients.items(), start=1):
        # Append the identifier to the patient_set.
        patient_set.append(idx)

        # Retrieve the dosing scheme identifier from the patient record.
        dosing_scheme_id = pat_data['DosingSchemeID']

        # Look up the corresponding dosing scheme record.
        scheme = dosing_scheme_dict[dosing_scheme_id]

        # Construct the phi by taking the Radiopharmaceutical field.
        phi[idx] = scheme['Radiopharmaceutical']

        # For dose_MBq, use the patient's AdministeredDose.
        dose_MBq[idx] = pat_data['AdministeredDose']

        # For uptake and imaging values, convert the string numbers to integers.
        u1[idx] = int(scheme['Uptake1']) // STEP_MINUTES
        i1[idx] = int(scheme['Imaging1']) // STEP_MINUTES
        u2[idx] = int(scheme['Uptake2']) // STEP_MINUTES
        i2[idx] = int(scheme['Imaging2']) // STEP_MINUTES

        patient_identifiers.append(pat_key)

    # Print the results.
    print("patient_set =", patient_set)
    print(patient_identifiers)
    print("phi =", phi)
    print("dose_MBq =", dose_MBq)
    print("u1 =", u1)
    print("i1 =", i1)
    print("u2 =", u2)
    print("i2 =", i2)

    # -----------------------
    # Radiopharmaceutical Definitions (Unchanged)
    # -----------------------
    # half_life = {
    #     'Ga68': 68.0, 'F18': 109.8, 'C11': 20.4, 'O15': 2.0, 'N13': 10.0
    # }
    # cost_per_GBq = {
    #     'Ga68': 0.0,   # Cost is now associated with the run itself, maybe 0 if fixed cost
    #     'F18' : 100.0, 'C11' : 300.0, 'O15' : 500.0, 'N13' : 400.0
    # }
    # # Availability windows: Ga68 availability is now determined by generator runs
    # pharma_avail = {
    #     'Ga68': [], # No direct purchase/availability window needed
    #     'F18' : [0,12,25],
    #     'C11' : [2,4,8,10,14],
    #     'O15' : [1,5,7,11,13,17],
    #     'N13' : [0,5,10,15]
    # }
    #
    # # -----------------------
    # # Patient Definitions (Unchanged)
    # # -----------------------
    # patient_set = [1,2,3]
    # phi = {1: 'Ga68', 2: 'F18', 3: 'F18'}
    # dose_MBq = {1: 150.0, 2: 280.0, 3: 50.0}
    # u1 = {1: 0,  2:  0,  3: 12}
    # i1 = {1: 4,  2: 12, 3:  5}
    # u2 = {1: 18, 2:  0,  3:  0}
    # i2 = {1: 4,  2:  0,  3:  0}

    ###########################################################################
    # 2. Pre-calculation for Ga-68 Generation Runs (Method 5)
    ###########################################################################
    print("Pre-calculating possible Ga-68 generation runs...")
    possible_runs = []
    run_id_counter = 0
    # Min duration is 1 step (e.g., 5 mins)
    for t_start in range(NUM_STEPS):
        for run_duration_steps in range(1, MAX_GEN_RUN_STEPS + 1):
            # Calculate timing for this potential run
            warmup_end_time   = t_start + GEN_WARMUP
            run_end_time      = warmup_end_time + run_duration_steps
            cooldown_end_time = run_end_time + GEN_COOLDOWN
            availability_time = run_end_time # Yield available when run finishes

            # Check if the entire cycle fits within the time horizon
            if cooldown_end_time <= NUM_STEPS:
                # Calculate yield for this duration
                yield_mbq = single_run_ga68_activity(
                    equilibrium_MBq=current_equilibrium_MBq,
                    run_steps=run_duration_steps,
                    step_minutes=STEP_MINUTES,
                    half_life_Ga68_minutes=half_life_Ga68_minutes
                )

                # Determine the busy interval (inclusive start, exclusive end)
                busy_interval_steps = list(range(t_start, cooldown_end_time))

                # Store the run details
                possible_runs.append({
                    'id': run_id_counter,
                    'start_time': t_start,
                    'run_duration_steps': run_duration_steps,
                    'yield_MBq': yield_mbq,
                    'availability_time': availability_time,
                    'busy_interval': busy_interval_steps,
                    # Add other times if needed for debugging/display
                    'warmup_end': warmup_end_time,
                    'run_end': run_end_time,
                    'cooldown_end': cooldown_end_time,
                })
                run_id_counter += 1

    print(f"Generated {len(possible_runs)} possible Ga-68 runs.")

    # --- Prepare data structures for Pyomo model ---
    run_indices = [run['id'] for run in possible_runs]
    run_yield = {run['id']: run['yield_MBq'] for run in possible_runs}
    run_availability_time = {run['id']: run['availability_time'] for run in possible_runs}

    # Helper map: time step -> list of run IDs busy at that time
    time_to_busy_runs_map = defaultdict(list)
    for run in possible_runs:
        for t_busy in run['busy_interval']:
            time_to_busy_runs_map[t_busy].append(run['id'])

    # Helper map: time step -> list of run IDs becoming available at that time
    avail_time_to_runs_map = defaultdict(list)
    for run in possible_runs:
        avail_time_to_runs_map[run['availability_time']].append(run['id'])

    ###########################################################################
    # 3. Build the Pyomo Model
    ###########################################################################
    model = ConcreteModel("PET_Schedule_with_Ga68Generator_Method5")

    # -----------------------
    # Sets
    # -----------------------
    model.T = RangeSet(0, NUM_STEPS - 1)  # Time steps
    model.P = Set(initialize=patient_set) # Patients
    model.F = Set(initialize=pharma_set)  # Pharmas (isotopes)
    model.R = Set(initialize=run_indices) # Set of possible Ga-68 runs

    # Define subset of Pharma excluding Ga68
    model.F_Other = Set(initialize=[f for f in pharma_set if f != 'Ga68'])

    # -----------------------
    # Parameters (Store pre-calculated run data)
    # -----------------------
    model.half_life = Param(model.F, initialize=half_life)
    model.cost      = Param(model.F, initialize=cost_per_GBq) # Note: Ga68 cost is 0 here

    # Store run-specific data
    model.RunYield = Param(model.R, initialize=run_yield)
    model.RunAvailabilityTime = Param(model.R, initialize=run_availability_time)

    # Store helper maps directly (or pass them to rule functions)
    # Using Python variables directly in rules is often simpler
    # model.TimeToBusyRuns = Param(model.T, initialize=time_to_busy_runs_map, default=[]) # Requires complex initialization
    # model.AvailTimeToRuns = Param(model.T, initialize=avail_time_to_runs_map, default=[])

    # Patient dose and durations
    model.phi      = phi
    model.dose_MBq = dose_MBq
    model.u1       = u1; model.i1 = i1; model.u2 = u2; model.i2 = i2

    ###########################################################################
    # 4. Define Decision Variables
    ###########################################################################
    # (A) Patient Start: S[p,t] = 1 if patient p starts exactly at time t
    model.S = Var(model.P, model.T, domain=Binary)

    # (B) Amount purchased x[f,t] in MBq (ONLY for f != 'Ga68')
    model.x = Var(model.F_Other, model.T, domain=NonNegativeReals)

    # (C) Inventory for each pharma (f,t)
    model.I = Var(model.F, model.T, domain=NonNegativeReals)

    # (D) Ga-68 Generator Run Selection
    model.ExecuteRun = Var(model.R, domain=Binary)

    # (E) Helper Variable: Total Ga-68 generated and becoming available at time t
    model.GeneratedAmountAvailableAt_t = Var(model.T, domain=NonNegativeReals)

    ###########################################################################
    # 5. Define Constraints
    ###########################################################################

    # -----------------------
    # 5.1 Patient Time Horizon Constraint (Unchanged)
    # -----------------------
    def patient_time_horizon_rule(m, p, t):
        finish_time = t + m.u1[p] + m.i1[p] + m.u2[p] + m.i2[p]
        # Use <= NUM_STEPS because time steps are 0 to NUM_STEPS-1
        # Finish time represents the start of the step AFTER the last activity.
        if finish_time <= NUM_STEPS:
            return pyo.Constraint.Skip
        else:
            return m.S[p, t] == 0
    model.PatientTimeHorizon = Constraint(model.P, model.T, rule=patient_time_horizon_rule)

    # -----------------------
    # 5.2 Patient Scheduling Constraints (Unchanged)
    # -----------------------
    # (A) Each patient must start exactly once
    def rule_one_start_per_patient(m, p):
        return sum(m.S[p, t] for t in m.T) == 1
    model.EachPatientOnce = Constraint(model.P, rule=rule_one_start_per_patient)

    # (B) No scanner overlap in imaging intervals
    def rule_no_scanner_overlap(m, p1, p2, t1, t2):
        if p1 >= p2: return pyo.Constraint.Skip
        p1_im1_s = t1 + m.u1[p1]; p1_im1_e = p1_im1_s + m.i1[p1]
        p1_im2_s = t1 + m.u1[p1] + m.i1[p1] + m.u2[p1]; p1_im2_e = p1_im2_s + m.i2[p1]
        p2_im1_s = t2 + m.u1[p2]; p2_im1_e = p2_im1_s + m.i1[p2]
        p2_im2_s = t2 + m.u1[p2] + m.i1[p2] + m.u2[p2]; p2_im2_e = p2_im2_s + m.i2[p2]

        # Check overlap for Image1(p1) vs Image1(p2) OR Image2(p2)
        overlap = False
        if m.i1[p1] > 0:
             if m.i1[p2] > 0 and max(p1_im1_s, p2_im1_s) < min(p1_im1_e, p2_im1_e): overlap = True
             if m.i2[p2] > 0 and max(p1_im1_s, p2_im2_s) < min(p1_im1_e, p2_im2_e): overlap = True
        # Check overlap for Image2(p1) vs Image1(p2) OR Image2(p2)
        if m.i2[p1] > 0:
             if m.i1[p2] > 0 and max(p1_im2_s, p2_im1_s) < min(p1_im2_e, p2_im1_e): overlap = True
             if m.i2[p2] > 0 and max(p1_im2_s, p2_im2_s) < min(p1_im2_e, p2_im2_e): overlap = True

        if overlap:
            return m.S[p1, t1] + m.S[p2, t2] <= 1
        else:
            return pyo.Constraint.Skip
    model.NoOverlap = Constraint(model.P, model.P, model.T, model.T, rule=rule_no_scanner_overlap)

    # -----------------------
    # 5.3 Inventory Constraints
    # -----------------------
    # (A) Inventory Balance for OTHER isotopes (f != 'Ga68')
    def inventory_balance_other_rule(m, f, t):
        decay_factor = 2 ** (-(STEP_MINUTES / m.half_life[f]))
        demand_t = sum(m.dose_MBq[p] * m.S[p, t] for p in m.P if m.phi[p] == f)
        purchase_t = m.x[f, t] # Purchase amount for non-Ga68 isotopes
        if t == 0:
            # Assuming no initial inventory
            return m.I[f, 0] == purchase_t - demand_t
        else:
            return m.I[f, t] == m.I[f, t - 1] * decay_factor + purchase_t - demand_t
    model.InventoryBalanceOther = Constraint(model.F_Other, model.T, rule=inventory_balance_other_rule)

    # (B) Inventory Balance for Ga68
    def inventory_balance_ga68_rule(m, t):
        f = 'Ga68'
        if f not in m.half_life:
            return pyo.Constraint.Skip
        decay_factor = 2 ** (-(STEP_MINUTES / m.half_life[f]))
        demand_t = sum(m.dose_MBq[p] * m.S[p, t] for p in m.P if m.phi[p] == f)
        # Generated amount becoming available at this time step t
        generated_t = m.GeneratedAmountAvailableAt_t[t]
        if t == 0:
            # Assuming no initial inventory
            return m.I[f, 0] == generated_t - demand_t
        else:
            return m.I[f, t] == m.I[f, t - 1] * decay_factor + generated_t - demand_t
    model.InventoryBalanceGa68 = Constraint(model.T, rule=inventory_balance_ga68_rule)

    # (C) Ensure sufficient inventory for demand (Applies to ALL isotopes)
    def sufficient_inventory_rule(m, f, t):
         # Demand at time t must be met by inventory *at the beginning* of step t + generation/purchase at t
         # Inventory m.I[f,t] represents inventory at the *end* of step t
         # Demand happens *during* step t. Let's check inventory from t-1 (decayed) + production/purchase at t
         decay_factor = 2 ** (-(STEP_MINUTES / m.half_life[f]))
         demand_t = sum(m.dose_MBq[p] * m.S[p, t] for p in m.P if m.phi[p] == f)

         inventory_at_start_of_t = 0
         if t > 0:
             inventory_at_start_of_t = m.I[f, t-1] * decay_factor

         production_at_t = 0
         if f == 'Ga68':
             production_at_t = m.GeneratedAmountAvailableAt_t[t]
         elif f in m.F_Other:
             production_at_t = m.x[f, t]

         # Ensure inventory available *before* demand is taken is sufficient
         return inventory_at_start_of_t + production_at_t >= demand_t

    model.SufficientInventory = Constraint(model.F, model.T, rule=sufficient_inventory_rule)


    # -----------------------
    # 5.4 Availability Constraints (Only for OTHER isotopes)
    # -----------------------
    def rule_availability_other(m, f, t):
        if t not in pharma_avail[f]:
            return m.x[f, t] == 0
        else:
            # Optional: Add upper bound on purchase if needed
            # return m.x[f, t] <= MAX_PURCHASE_AMOUNT
             return pyo.Constraint.Skip # Allow purchase if in availability window
    model.AvailConstrOther = Constraint(model.F_Other, model.T, rule=rule_availability_other)

    # -----------------------
    # 5.5 Ga-68 Generator Constraints (Method 5 Implementation)
    # -----------------------
    # (A) Link Executed Runs to Generated Amount Available
    def rule_yield_aggregation(m, t):
       # Runs becoming available at time t
       contributing_runs = avail_time_to_runs_map.get(t, [])
       return m.GeneratedAmountAvailableAt_t[t] == sum(m.ExecuteRun[r] * m.RunYield[r] for r in contributing_runs)
    model.YieldAggregation = Constraint(model.T, rule=rule_yield_aggregation)

    # (B) Prevent Overlapping Generator Usage
    def rule_gen_non_overlap(m, t):
       # Runs busy during time step t
       busy_runs = time_to_busy_runs_map.get(t, [])
       return sum(m.ExecuteRun[r] for r in busy_runs) <= 1
    model.GenNonOverlap = Constraint(model.T, rule=rule_gen_non_overlap)

    ###########################################################################
    # 6. Objective Function: Minimize Purchase Cost
    ###########################################################################
    # Cost only includes purchased isotopes (F_Other). Assumes Ga-68 generation cost is fixed/neglected.
    # If Ga-68 runs have a cost, add sum(m.ExecuteRun[r] * CostPerRun[r] for r in m.R)
    def total_cost_rule(m):
        # Cost is per GBq, x is in MBq. Divide x by 1000.
        return sum(m.cost[f] * (m.x[f, t] / 1000.0) for f in m.F_Other for t in m.T)
    model.TotalCost = Objective(rule=total_cost_rule, sense=pyo.minimize)

    ###########################################################################
    # 7. Solve the Model
    ###########################################################################
    # Select a suitable solver (CBC is default open-source MILP solver with Pyomo)
    # Other options: glpk, gurobi (requires license and installation), cplex, etc.
    solver_name = 'cbc'
    opt = SolverFactory(solver_name)

    if opt.available(exception_flag=False):
        print(f"\n--- Solving with {solver_name} ---")
        try:
             # Add time limit if needed: opt.options['sec'] = 600 # 10 minutes
             res = opt.solve(model, tee=True) # tee=True shows solver output
             print("\nSolver status:", res.solver.status)
             print("Solver termination:", res.solver.termination_condition)
             solved = True
        except Exception as e:
             print(f"Error during solve: {e}")
             solved = False
             res = None # Ensure res exists
    else:
        print(f"Solver {solver_name} is not available.")
        solved = False
        res = None

    ###########################################################################
    # 8. Display and Analyze Results
    ###########################################################################
    print("\n========== Results ==========\n")
    if solved and res and (res.solver.termination_condition == pyo.TerminationCondition.optimal or res.solver.termination_condition == pyo.TerminationCondition.feasible):
        print(f"Objective (min cost): {pyo.value(model.TotalCost):.3f}\n")

        # Patient scheduling
        print("--- Patient Schedule ---")
        scheduled_patients = False
        for p in model.P:
            for t in model.T:
                if pyo.value(model.S[p, t]) > 0.5:
                    print(f"Patient {p}: Starts at t={t} (Minute {t*STEP_MINUTES}), Pharma={model.phi[p]}, Dose={model.dose_MBq[p]} MBq")
                    scheduled_patients = True
        if not scheduled_patients:
             print("No patients scheduled (check constraints or problem feasibility).")


        # Purchases (Other Isotopes)
        print("\n--- Purchases x[f,t] (MBq) ---")
        purchased = False
        for f in model.F_Other:
            for t in model.T:
                val = pyo.value(model.x[f, t])
                if val > 1e-6:
                    print(f"  t={t:2d} (Min {t*STEP_MINUTES}), {f}: {val:.1f} MBq")
                    purchased = True
        if not purchased:
            print("No isotopes purchased.")

        # Ga68 Generator usage
        print("\n--- Ga68 Generator Runs Selected ---")
        runs_executed = False
        total_yield = 0
        ga68_generator = []
        for r in model.R:
            if pyo.value(model.ExecuteRun[r]) > 0.5:
                run_info = next(item for item in possible_runs if item["id"] == r)
                print(f"  Run {r}: Start Cycle t={run_info['start_time']} (Min {run_info['start_time']*STEP_MINUTES}), "
                      f"Duration={run_info['run_duration_steps']} steps ({run_info['run_duration_steps']*STEP_MINUTES} mins), "
                      f"Yield={run_info['yield_MBq']:.1f} MBq, "
                      f"Available at t={run_info['availability_time']} (Min {run_info['availability_time']*STEP_MINUTES})")
                ga68_generator.append((run_info['start_time']*STEP_MINUTES,run_info['run_duration_steps']*STEP_MINUTES))
                runs_executed = True
                total_yield += run_info['yield_MBq']
        if not runs_executed:
             print("No Ga68 generator runs selected.")
        else:
             print(f"Total Ga68 Yield from selected runs: {total_yield:.1f} MBq")

        # # Optional: Display Inventory Levels (can be long)
        # print("\n--- Inventory I[f,t] (MBq) ---")
        # for t in model.T:
        #     inventory_line = f"t={t:2d}: "
        #     has_inventory = False
        #     for f in model.F:
        #          val = pyo.value(model.I[f, t])
        #          if val > 1e-4:
        #               inventory_line += f"{f}={val:.1f}  "
        #               has_inventory = True
        #     if has_inventory:
        #          print(inventory_line)

        schedules = {}
        for p in model.P:
            for t in model.T:
                if pyo.value(model.S[p, t]) > 0.5:
                    schedules[patient_identifiers[p - 1]] = t * STEP_MINUTES

        return schedules, ga68_generator

    elif solved and res:
         print(f"Solver finished but did not find an optimal/feasible solution. Status: {res.solver.status}, Condition: {res.solver.termination_condition}")
         return None
    else:
         print("Model solving failed or solver was unavailable.")
         return None

    print("\n============================\n")

# Example run command: python your_script_name.py

if __name__ == '__main__':
    cbc()