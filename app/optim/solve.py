#!/usr/bin/env python3
import numpy as np
import pyomo.environ as pyo
from pyomo.environ import ConcreteModel, Set, Param, Var, Binary, NonNegativeReals, Integers, Objective, Constraint, RangeSet, Reals
from pyomo.opt import SolverFactory
import math

if __name__ == '__main__':

    ###############################################################################
    # Example data
    ###############################################################################

    # Discrete time steps: each step = 5 minutes
    # We'll define a horizon of 96 steps => 480 minutes total = 8h
    NUM_STEPS = 50
    time_set = range(NUM_STEPS)

    # Warm-up & cool-down times (in discrete steps) for the 68Ga generator
    GEN_WARMUP  = 1
    GEN_COOLDOWN= 1

    # Constants
    half_life_Ge68_days = 270.8
    half_life_Ga68_minutes = 67.71

    def compute_ga68_activity(initial_activity_GBq, days_since_calibration, production_time_minutes=5):
        # Decay constants
        lambda_Ge68 = np.log(2) / half_life_Ge68_days
        lambda_Ga68 = np.log(2) / half_life_Ga68_minutes

        # Current Ge68 activity in MBq
        current_Ge68_activity_MBq = initial_activity_GBq * 1000 * np.exp(-lambda_Ge68 * days_since_calibration)

        # Ga68 activity produced after a given time (assuming equilibrium and full extraction)
        ga68_activity_MBq = current_Ge68_activity_MBq * (1 - np.exp(-lambda_Ga68 * production_time_minutes))

        return ga68_activity_MBq

    # Example usage
    initial_activity_GBq = 1.85  # Example: 1.85 GBq generator
    elapsed_days_since_calibration = 30  # Example: 30 days since calibration
    produced_activity = compute_ga68_activity(initial_activity_GBq, elapsed_days_since_calibration)

    # For a more accurate or custom function, you can define more (x,g(x)) breakpoints.
    def cumulative_ga68_activity(Q, steps, step_minutes=5):
        lambda_Ga68 = np.log(2) / half_life_Ga68_minutes
        cumulative_activity = 0
        for i in range(steps):
            cumulative_activity *= np.exp(-lambda_Ga68 * step_minutes)
            cumulative_activity += Q
        return cumulative_activity


    Ge68_num_steps = NUM_STEPS // 2
    max_Q = compute_ga68_activity(initial_activity_GBq, elapsed_days_since_calibration, 5)

    PW_values = [n for n in range(Ge68_num_steps + 1)]
    PW_breakpoints = [
        cumulative_ga68_activity(max_Q, n, step_minutes=5)
        for n in PW_values
    ]

    # Radiopharmaceuticals and their half-lives (in minutes),
    # plus cost (currency per GBq),
    # plus availability windows (the set of time steps where you can buy or produce them).
    # We illustrate with 5 common isotopes: Ga68, F18, C11, O15, N13
    # and define some arbitrary cost and availability sets. Adjust as needed.
    pharma_set = ['Ga68','F18','C11','O15','N13']

    half_life = {
        'Ga68': 68.0,
        'F18': 109.8,
        'C11': 20.4,
        'O15': 2.0,
        'N13': 10.0
    }

    cost_per_GBq = {
        'Ga68': 250.0,  # example cost for Ga-68
        'F18' : 100.0,
        'C11' : 300.0,
        'O15' : 500.0,
        'N13' : 400.0
    }

    # For demonstration, assume each is "available" in some subset of times
    # (If a time is not in the set, x_{f,t} must be 0.)
    pharma_avail = {
        'Ga68': list(time_set),          # we "produce" Ga-68 (with generator constraints)
        'F18' : [0,3,6,9,12,15, 18, 20, 23, 25, 28],         # can only be delivered at these times
        'C11' : [2,4,8,10,14],           # ...
        'O15' : [1,5,7,11,13,17],
        'N13' : [0,5,10,15]
    }

    # Patients
    # For each patient p, define:
    #  - required pharma: phi[p]
    #  - required dose (MBq): dose[p]
    #  - durations: u1[p], i1[p], u2[p], i2[p] in discrete time steps
    patient_set = [1,2,3]

    phi = {
        1: 'Ga68',
        2: 'F18',
        3: 'F18'
    }

    dose_MBq = {
        1: 150.0,
        2: 280.0,
        3: 50.0
    }

    # durations (in steps). 1 step = 5 min
    # e.g. patient 1 has 1 step uptake1, 2 steps imaging1, 1 step uptake2, 2 steps imaging2
    u1 = {1:0, 2:0, 3:12}
    i1 = {1:4, 2:12, 3:5}
    u2 = {1:18, 2:0, 3:0}
    i2 = {1:4, 2:0, 3:0}

    ###############################################################################
    # Create a Pyomo model
    ###############################################################################
    model = ConcreteModel("PET_Schedule_with_Ga68Generator")

    # Sets
    model.T = RangeSet(0, NUM_STEPS-1)
    model.P = Set(initialize=patient_set)
    model.F = Set(initialize=pharma_set)

    # Parameters
    model.half_life = Param(model.F, initialize=half_life, mutable=True)
    model.cost      = Param(model.F, initialize=cost_per_GBq, mutable=True)

    # Availabilities: we'll encode it by forcing x_{f,t} = 0 if t not in pharma_avail[f]
    def can_buy_filter(model, t, f):
        return (t in pharma_avail[f])

    model.CanBuy = Set(
        model.F,
        within=model.T,
        initialize=lambda m,f: [tt for tt in m.T if tt in pharma_avail[f]],
        filter=can_buy_filter
    )

    # Patient dose and durations as Param/Dict
    model.phi     = phi    # dictionary: patient -> required pharma
    model.dose_MBq= dose_MBq
    model.u1 = u1
    model.i1 = i1
    model.u2 = u2
    model.i2 = i2

    # Decision Variables

    # (1) Patient start time: S_{p,t} binary => 1 if patient p starts exactly at time t
    model.S = Var(model.P, model.T, domain=Binary, initialize=0.0)

    # (2) Amount purchased/produced x_{f,t} in MBq
    #     We'll force x_{f,t}=0 if t not in availability set of f (below).
    model.x = Var(model.F, model.T, domain=NonNegativeReals, initialize=0.0)

    # (3) We will define a "start of Ga68 generator" variable Gstart[t] = 1 if we
    #     initiate a generation block at time t. Then the generator is busy
    #     from t to t + warm-up + generation_time + cool-down - 1.
    model.Gstart = Var(model.T, domain=Binary, initialize=0.0)

    # (4) An integer variable for the generation time length Gtime[t], in *discrete steps*,
    #     triggered if we produce Ga-68 at time t. We'll approximate it with piecewise.
    model.Gtime = Var(model.T, domain=Integers, bounds=(0, Ge68_num_steps), initialize=0.0)  # some upper bound

    # NEW: Inventory variable for each pharma at each time step
    model.I = Var(model.F, model.T, domain=NonNegativeReals, initialize=0.0)


    # Constraint to ensure patient schedule fits within the NUM_STEPS horizon
    def patient_time_horizon_rule(m, p, t):
        finish_time = t + m.u1[p] + m.i1[p] + m.u2[p] + m.i2[p]
        if finish_time <= NUM_STEPS:
            return pyo.Constraint.Skip
        else:
            return m.S[p, t] == 0
    model.PatientTimeHorizon = Constraint(model.P, model.T, rule=patient_time_horizon_rule)


    # We also need to link x['Ga68', t] with Gstart[t] and Gtime[t].
    # We'll do that with piecewise constraints and with a "no overlap" generator constraint.

    ###############################################################################
    # Piecewise definition for Gtime[t] = g( x['Ga68',t] ), using breakpoints
    ###############################################################################
    # We can use a Pyomo Piecewise to define Gtime[t] = PW_values(...) wrt x_{Ga68,t}.
    # The piecewise function is stepwise or linear. Let's do stepwise (SOC = 'EQ').
    #   x in [0, 100] => Gtime = 1, etc.
    # If you prefer linear interpolation, you could use other options.

    def Gtime_piecewise_rule(b, t):
        m = b.model()
        return pyo.Piecewise(
            m.Gtime[t],  # output variable
            m.x['Ga68', t],  # single domain variable at index t
            pw_pts=PW_breakpoints,  # breakpoints for x
            pw_constr_type='EQ',  # enforce exact piecewise matching
            f_rule=PW_values,  # corresponding outputs
            pw_repn='SOS2'  # piecewise representation
        )

    model.Gtime_piecewise = pyo.Block(model.T, rule=Gtime_piecewise_rule)

    # Alternatively, if you prefer direct big-M logic, you could code explicit
    # constraints. Piecewise is a cleaner approach in Pyomo.

    ###############################################################################
    # (A) Each patient starts exactly once
    ###############################################################################
    def rule_one_start_per_patient(m, p):
        return sum(m.S[p,t] for t in m.T) == 1
    model.EachPatientOnce = Constraint(model.P, rule=rule_one_start_per_patient)

    ###############################################################################
    # (B) No scanner overlap in imaging intervals
    ###############################################################################
    # If patient p starts at time t, its imaging intervals are:
    #   [t + u1[p],   t + u1[p] + i1[p])   for imaging1
    #   [t + u1[p] + i1[p] + u2[p], t + u1[p] + i1[p] + u2[p] + i2[p]) for imaging2
    #
    # We'll do a time-indexed approach: For any pair of start times (t1, t2) that
    # would cause overlap, we add constraint: S[p,t1] + S[q,t2] <= 1
    # We skip if there's no possibility of overlap in the discrete timeline.

    def rule_no_scanner_overlap(m, p1, p2, t1, t2):
        if p1 >= p2:
            return pyo.Constraint.Skip  # avoid duplicates and p1=p2
        # Calculate the imaging intervals for p1
        p1_im1_start = t1 + m.u1[p1]
        p1_im1_end   = t1 + m.u1[p1] + m.i1[p1]
        p1_im2_start = t1 + m.u1[p1] + m.i1[p1] + m.u2[p1]
        p1_im2_end   = t1 + m.u1[p1] + m.i1[p1] + m.u2[p1] + m.i2[p1]

        # for p2
        p2_im1_start = t2 + m.u1[p2]
        p2_im1_end   = t2 + m.u1[p2] + m.i1[p2]
        p2_im2_start = t2 + m.u1[p2] + m.i1[p2] + m.u2[p2]
        p2_im2_end   = t2 + m.u1[p2] + m.i1[p2] + m.u2[p2] + m.i2[p2]

        # Combined interval for p1 imaging: [p1_start, p1_end)
        # We'll define min, max just to unify
        p1_start_im = p1_im1_start
        p1_end_im   = p1_im2_end

        p2_start_im = p2_im1_start
        p2_end_im   = p2_im2_end

        # Check if there's a potential overlap in discrete steps
        # Overlap occurs if intervals intersect in time
        if (p1_start_im < p2_end_im) and (p2_start_im < p1_end_im):
            # Then we must forbid them from both happening
            return m.S[p1,t1] + m.S[p2,t2] <= 1
        else:
            return pyo.Constraint.Skip

    model.NoOverlap = Constraint(
        model.P, model.P, model.T, model.T,
        rule=rule_no_scanner_overlap
    )


    # (C) Inventory Balance (replaces the old “DoseConstraint”)
    #     I[f,t] = Decayed inventory from (t-1) + new x[f,t] − sum of demands at t

    def inventory_balance_rule(m, f, t):
        decay_factor = 2 ** (- (5.0 / m.half_life[f]))
        demand_t = sum(m.dose_MBq[p] * m.S[p, t] for p in m.P if m.phi[p] == f)

        if t == 0:
            # At t=0, no previous inventory, so:
            return m.I[f, 0] == m.x[f, 0] - demand_t
        else:
            return m.I[f, t] == m.I[f, t - 1] * decay_factor + m.x[f, t] - demand_t
    model.InventoryBalance = Constraint(model.F, model.T, rule=inventory_balance_rule)


    ###############################################################################
    # (D) Radiopharmaceutical availability
    ###############################################################################
    # If (f,t) not in pharma_avail, force x[f,t] = 0
    def rule_availability(m, f, t):
        if t not in pharma_avail[f]:
            return m.x[f,t] == 0
        return pyo.Constraint.Skip

    model.AvailConstr = Constraint(
        model.F, model.T,
        rule=rule_availability
    )

    ###############################################################################
    # Objective: Minimize total cost
    ###############################################################################
    # cost = sum_f sum_t (cost_f * x[f,t] in GBq)
    # we have x in MBq, so we do x/1000 => GBq
    def total_cost_rule(m):
        return sum( m.cost[f] * (m.x[f,t]/1000.0) for f in m.F for t in m.T )
    model.TotalCost = pyo.Objective(rule=total_cost_rule, sense=pyo.minimize)

    ###############################################################################
    # Solve
    ###############################################################################
    opt = SolverFactory('cbc')   # or 'glpk'
    res = opt.solve(model, tee=True)

    print("\nSolver status:", res.solver.status)
    print("Solver termination:", res.solver.termination_condition)

    ###############################################################################
    # Display results
    ###############################################################################
    print("\n========== Results ==========\n")
    print(f"Objective (min cost): {pyo.value(model.TotalCost):.3f}\n")

    # Patient scheduling
    for p in model.P:
        for t in model.T:
            if pyo.value(model.S[p,t]) > 0.5:
                print(f"Patient {p} starts at time {t} (minutes={t*5}) -> pharma={phi[p]}, dose={dose_MBq[p]}MBq")

    print("\nPurchases/Productions x[f,t]: (MBq)")
    for f in model.F:
        for t in model.T:
            val = pyo.value(model.x[f,t])
            if val > 1e-6:
                print(f"  t={t:2d}, {f}: {val:.1f} MBq")

    print("\nGa68 Generator usage:")
    for t in model.T:
        if pyo.value(model.Gstart[t]) > 0.5:
            print(f"  Start Ga68 production at t={t}, Gtime={pyo.value(model.Gtime[t])} steps -> total block = warmup({GEN_WARMUP}) + {pyo.value(model.Gtime[t])} + cooldown({GEN_COOLDOWN}) = {GEN_WARMUP + pyo.value(model.Gtime[t]) + GEN_COOLDOWN}")

