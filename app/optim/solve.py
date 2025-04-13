#!/usr/bin/env python3

import numpy as np
import pyomo.environ as pyo
from pyomo.environ import (
    ConcreteModel, Set, Param, Var, Binary, NonNegativeReals,
    NonNegativeIntegers, Integers, Objective, Constraint, RangeSet, Reals
)
from pyomo.opt import SolverFactory
import math

###############################################################################
# Helper Functions
###############################################################################

def compute_ga68_activity(
    initial_activity_GBq,
    days_since_calibration,
    production_time_minutes=5,
    half_life_Ge68_days=270.8,
    half_life_Ga68_minutes=67.71
):
    """
    Compute the equilibrium Ga-68 activity (in MBq) extracted from a Ge-68/Ga-68
    generator after a specified production time, given:
      - initial_activity_GBq: the generator's initial Ge-68 activity in GBq,
      - days_since_calibration: elapsed days since the generator's reference calibration,
      - production_time_minutes: time in minutes for a single generation run,
      - half_life_Ge68_days: Ge-68 half-life in days (default ~270.8),
      - half_life_Ga68_minutes: Ga-68 half-life in minutes (default ~67.71).
    """
    # Decay constants (lambda)
    lambda_Ge68 = np.log(2) / half_life_Ge68_days
    lambda_Ga68 = np.log(2) / half_life_Ga68_minutes

    # Current Ge-68 activity in MBq (initially in GBq → multiply by 1000 to get MBq)
    current_Ge68_activity_MBq = initial_activity_GBq * 1000.0 * np.exp(-lambda_Ge68 * days_since_calibration)

    # Ga-68 activity produced after 'production_time_minutes'
    # Assuming equilibrium is reached and entire daughter activity is extracted
    ga68_activity_MBq = current_Ge68_activity_MBq * (1 - np.exp(-lambda_Ga68 * production_time_minutes))

    return ga68_activity_MBq

def single_run_ga68_activity(
    equilibrium_MBq,
    run_steps,
    step_minutes=5,
    half_life_Ga68_minutes=67.71
):
    """
    Returns the Ga-68 (MBq) extracted in a single generator run of 'run_steps'
    time-steps (each step being 'step_minutes' long).
    Uses the usual formula:  eq * (1 - exp(-lambda * t)).
    """
    lambda_Ga68 = np.log(2) / half_life_Ga68_minutes
    total_minutes = run_steps * step_minutes

    # If you wait long enough, the generator can produce 'equilibrium_MBq' in one shot.
    # The extracted portion is given by (1 - exp(-lambda * total_minutes)).
    extracted = equilibrium_MBq * (1 - np.exp(-lambda_Ga68 * total_minutes))
    return extracted

def Gtime_piecewise_rule(block, t):
    """
    Defines a piecewise relationship for the Ga-68 yield at time t:
      yield_ga68[t] = function(Gtime[t])
    Gtime[t] is the integer generation runtime in discrete steps,
    yield_ga68[t] is the total MBq extracted from the generator for that run.
    """
    m = block.model()
    return pyo.Piecewise(
        m.x['Ga68', t],   # "output" variable of the piecewise
        m.Gtime[t],       # "input" variable
        pw_pts=m.PW_values,
        f_rule=m.PW_breakpoints,
        pw_constr_type='EQ',  # Must match the piecewise function exactly
        pw_repn='SOS2'
    )


###############################################################################
# Main Script
###############################################################################

if __name__ == '__main__':
    ###########################################################################
    # 1. Define Data and Parameters
    ###########################################################################

    # -----------------------
    # Time Horizon
    # -----------------------
    # Discrete time steps: each step = 5 minutes.
    # Set NUM_STEPS = 50 => total 50 * 5 = 250 minutes = 4h10m (for example).
    NUM_STEPS = 50
    time_set = range(NUM_STEPS)

    # -----------------------
    # Ga-68 Generator Parameters
    # -----------------------
    GEN_WARMUP   = 1  # Steps required for warm-up
    GEN_COOLDOWN = 1  # Steps required for cool-down

    # Half-lives:
    half_life_Ge68_days    = 270.8
    half_life_Ga68_minutes = 67.71

    # For demonstration, suppose the generator was 1.85 GBq at calibration,
    # and 30 days have elapsed since that calibration.
    initial_activity_GBq = 1.85
    elapsed_days_since_calibration = 30

    # Compute max possible Ga-68 equilibrium activity (MBq) from the generator in 5 minutes.
    max_Q = compute_ga68_activity(
        initial_activity_GBq,
        elapsed_days_since_calibration,
        production_time_minutes=5,
        half_life_Ge68_days=half_life_Ge68_days,
        half_life_Ga68_minutes=half_life_Ga68_minutes
    )

    # Generate piecewise breakpoints for possible run lengths of the generator.
    # For example, if we let generator run from 0..(NUM_STEPS//2) steps, i.e. up to 25 steps in this example.
    Ge68_num_steps = NUM_STEPS // 2
    PW_values = list(range(Ge68_num_steps + 1))  # discrete steps: 0..25
    PW_breakpoints = [
        single_run_ga68_activity(
            equilibrium_MBq=max_Q,
            run_steps=n,
            step_minutes=5,
            half_life_Ga68_minutes=half_life_Ga68_minutes
        )
        for n in PW_values
    ]

    # -----------------------
    # Radiopharmaceutical Definitions
    # -----------------------
    # We'll define 5 isotopes for illustration:
    pharma_set = ['Ga68','F18','C11','O15','N13']
    half_life = {
        'Ga68': 68.0,    # minutes
        'F18': 109.8,
        'C11': 20.4,
        'O15': 2.0,
        'N13': 10.0
    }
    cost_per_GBq = {
        'Ga68': 250.0,   # (currency/GBq)
        'F18' : 100.0,
        'C11' : 300.0,
        'O15' : 500.0,
        'N13' : 400.0
    }
    # Availability windows for each isotope.
    # If a time is not in the list, we cannot buy/produce that isotope at that time.
    pharma_avail = {
        'Ga68': list(time_set),     # We'll produce Ga-68 via the generator
        'F18' : [0,3,6,9,12,15,18,20,23,25,28],
        'C11' : [2,4,8,10,14],
        'O15' : [1,5,7,11,13,17],
        'N13' : [0,5,10,15]
    }

    # -----------------------
    # Patient Definitions
    # -----------------------
    patient_set = [1,2,3]
    # For each patient, define which pharma is required and how much dose (MBq).
    phi = {   # which isotope each patient needs
        1: 'Ga68',
        2: 'F18',
        3: 'F18'
    }
    dose_MBq = {
        1: 150.0,
        2: 280.0,
        3: 50.0
    }

    # For each patient p, define durations of uptake (u1,p), imaging1 (i1,p), uptake2 (u2,p), imaging2 (i2,p),
    # expressed in discrete time steps (each step = 5 minutes).
    # For example, patient 1 has: 0 steps for the first uptake, 4 steps for imaging1,
    # 18 steps for second uptake, 4 steps for imaging2.
    u1 = {1: 0,  2:  0,  3: 12}
    i1 = {1: 4,  2: 12, 3:  5}
    u2 = {1: 18, 2:  0,  3:  0}
    i2 = {1: 4,  2:  0,  3:  0}

    ###########################################################################
    # 2. Build the Pyomo Model
    ###########################################################################

    model = ConcreteModel("PET_Schedule_with_Ga68Generator")

    # -----------------------
    # Sets
    # -----------------------
    model.T = RangeSet(0, NUM_STEPS - 1)  # Time steps
    model.P = Set(initialize=patient_set) # Patients
    model.F = Set(initialize=pharma_set)  # Pharmas (isotopes)

    # We store PW_values and PW_breakpoints in the model so that the piecewise
    # block can reference them easily.
    model.PW_values = PW_values
    model.PW_breakpoints = PW_breakpoints

    # -----------------------
    # Parameters
    # -----------------------
    model.half_life = Param(model.F, initialize=half_life, mutable=True)
    model.cost      = Param(model.F, initialize=cost_per_GBq, mutable=True)

    # We encode the availability sets so that if a time step 't' is not in
    # pharma_avail[f], we must set x[f,t] = 0.
    def can_buy_filter(model, t, f):
        return (t in pharma_avail[f])

    model.CanBuy = Set(
        model.F,
        within=model.T,
        initialize=lambda m,f: [tt for tt in m.T if tt in pharma_avail[f]],
        filter=can_buy_filter
    )

    # Patient dose and durations (from dictionary references).
    # In many Pyomo models, you could keep these in external dictionaries
    # or define them as Param. We'll just store them directly.
    model.phi      = phi
    model.dose_MBq = dose_MBq
    model.u1       = u1
    model.i1       = i1
    model.u2       = u2
    model.i2       = i2

    ###########################################################################
    # 3. Define Decision Variables
    ###########################################################################

    # (A) Patient Start: S[p,t] = 1 if patient p starts exactly at time t
    model.S = Var(model.P, model.T, domain=Binary, initialize=0.0)

    # (B) Amount purchased/produced x[f,t] in MBq
    # We will force x[f,t] = 0 if (f,t) is not in the availability set or
    # if we don't run the generator for Ga68.
    model.x = Var(model.F, model.T, domain=NonNegativeReals, initialize=0.0)

    # (F) Inventory for each pharma (f,t)
    #     How many MBq of isotope f are in stock at time t?
    model.I = Var(model.F, model.T, domain=NonNegativeReals, initialize=0.0)

    ###########################################################################
    # 4. Define Constraints
    ###########################################################################

    # -----------------------
    # 4.1 Patient Time Horizon Constraint
    # -----------------------
    # Ensures a patient cannot start if there's insufficient time left to complete
    # their entire protocol (u1 + i1 + u2 + i2) within the horizon.
    def patient_time_horizon_rule(m, p, t):
        finish_time = t + m.u1[p] + m.i1[p] + m.u2[p] + m.i2[p]
        if finish_time <= NUM_STEPS:
            return pyo.Constraint.Skip
        else:
            # Force S[p,t] = 0 if starting at t would exceed the horizon.
            return m.S[p, t] == 0
    model.PatientTimeHorizon = Constraint(model.P, model.T, rule=patient_time_horizon_rule)

    # -----------------------
    # 4.2 Ga68 Generator Constraints
    # -----------------------



    # -----------------------
    # 4.3 Patient Scheduling Constraints
    # -----------------------

    # (A) Each patient must start exactly once
    def rule_one_start_per_patient(m, p):
        return sum(m.S[p, t] for t in m.T) == 1
    model.EachPatientOnce = Constraint(model.P, rule=rule_one_start_per_patient)

    # (B) No scanner overlap in imaging intervals
    def rule_no_scanner_overlap(m, p1, p2, t1, t2):
        """
        If there is any time overlap in the imaging intervals for two distinct
        patients p1, p2, they cannot both start in those times (S[p1,t1] + S[p2,t2] ≤ 1).
        """
        if p1 >= p2:
            return pyo.Constraint.Skip  # Avoid duplicate or same-patient checks

        # Calculate imaging intervals for patient p1 starting at t1
        p1_im1_start = t1 + m.u1[p1]
        p1_im1_end   = t1 + m.u1[p1] + m.i1[p1]
        p1_im2_start = t1 + m.u1[p1] + m.i1[p1] + m.u2[p1]
        p1_im2_end   = t1 + m.u1[p1] + m.i1[p1] + m.u2[p1] + m.i2[p1]

        # Entire imaging span for p1: from start of first imaging to end of second imaging
        p1_start_im = p1_im1_start
        p1_end_im   = p1_im2_end

        # Same for patient p2
        p2_im1_start = t2 + m.u1[p2]
        p2_im1_end   = t2 + m.u1[p2] + m.i1[p2]
        p2_im2_start = t2 + m.u1[p2] + m.i1[p2] + m.u2[p2]
        p2_im2_end   = t2 + m.u1[p2] + m.i1[p2] + m.u2[p2] + m.i2[p2]

        p2_start_im = p2_im1_start
        p2_end_im   = p2_im2_end

        # Overlap check: intervals [p1_start_im, p1_end_im)
        #                vs [p2_start_im, p2_end_im)
        # If they do overlap, we forbid them from both starting in that configuration.
        if (p1_start_im < p2_end_im) and (p2_start_im < p1_end_im):
            return m.S[p1, t1] + m.S[p2, t2] <= 1
        else:
            return pyo.Constraint.Skip

    model.NoOverlap = Constraint(
        model.P, model.P, model.T, model.T,
        rule=rule_no_scanner_overlap
    )

    # -----------------------
    # 4.4 Inventory Constraints
    # -----------------------
    def inventory_balance_rule(m, f, t):
        # Decay factor for pharma f per time step (5 minutes)
        decay_factor = 2 ** (-(5.0 / m.half_life[f]))
        demand_t = sum(m.dose_MBq[p] * m.S[p, t] for p in m.P if m.phi[p] == f)

        if t == 0:
            return m.I[f, 0] == m.x[f, 0] - demand_t
        else:
            return m.I[f, t] == m.I[f, t - 1] * decay_factor + m.x[f, t] - demand_t

    model.InventoryBalance = Constraint(model.F, model.T, rule=inventory_balance_rule)

    # -----------------------
    # 4.5 Availability Constraints
    # -----------------------
    def rule_availability(m, f, t):
        if t not in pharma_avail[f]:
            return m.x[f, t] == 0
        else:
            return pyo.Constraint.Skip
    model.AvailConstr = Constraint(model.F, model.T, rule=rule_availability)

    ###########################################################################
    # 5. Objective Function: Minimize Total Cost
    ###########################################################################
    # cost = Σ_f Σ_t cost_f * (x[f,t] in GBq)
    # but x[f,t] is in MBq → convert MBq to GBq by dividing by 1000.
    def total_cost_rule(m):
        return sum(m.cost[f] * (m.x[f, t] / 1000.0) for f in m.F for t in m.T)
    model.TotalCost = Objective(rule=total_cost_rule, sense=pyo.minimize)

    ###########################################################################
    # 6. Solve the Model
    ###########################################################################
    opt = SolverFactory('cbc')   # or another solver, e.g. 'glpk' or 'gurobi'
    res = opt.solve(model, tee=True)

    print("\nSolver status:", res.solver.status)
    print("Solver termination:", res.solver.termination_condition)

    ###########################################################################
    # 7. Display and Analyze Results
    ###########################################################################
    print("\n========== Results ==========\n")
    print(f"Objective (min cost): {pyo.value(model.TotalCost):.3f}\n")

    # Patient scheduling
    for p in model.P:
        for t in model.T:
            if pyo.value(model.S[p, t]) > 0.5:
                print(f"Patient {p} starts at time {t} "
                      f"(actual minutes={t*5}) "
                      f"-> pharma={model.phi[p]}, "
                      f"dose={model.dose_MBq[p]} MBq")

    # Purchases/Productions
    print("\nPurchases/Productions x[f,t]: (MBq)")
    for f in model.F:
        for t in model.T:
            val = pyo.value(model.x[f, t])
            if val > 1e-6:  # Print only if significant
                print(f"  t={t:2d}, {f}: {val:.1f} MBq")

    # Ga68 Generator usage
    print("\nGa68 Generator usage:")