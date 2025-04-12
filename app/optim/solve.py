#!/usr/bin/env python3
from ortools.sat.python import cp_model
import math

def solve_pet_scheduling():
    model = cp_model.CpModel()

    # -----------------------------------------------------------
    # 1) Define data/instances
    # -----------------------------------------------------------
    # Time is in discrete steps of 5 minutes: T = {0..Tmax-1}
    Tmax = 20  # up to 20 discrete slots => 100 min total
    time_slots = range(Tmax)

    # Example: 2 patients
    # (UptakeDur1, ImagingDur1, UptakeDur2, ImagingDur2, DoseMBq, pharmRequired)
    patients_data = {
        0: (1, 2, 1, 2, 300, '18F'),  # needs 300 MBq, uses 18F
        1: (1, 2, 1, 2, 250, 'Ga'),   # needs 250 MBq, uses Ga
    }
    patients = list(patients_data.keys())

    # Radiopharmaceuticals
    # For each r: define half_life_minutes, cost_per_GBq, ...
    #  For r != Ga: we have available_slots. For Ga: produced by generator.
    pharm_data = {
        '18F': {
            'half_life': 109.8,
            'cost_per_GBq': 100.0,
            'available_slots': [0, 4, 8],  # can purchase at t=0,4,8
        },
        'Ga': {
            'half_life': 68.0,
            'cost_per_GBq': 200.0,
        }
    }
    radio_list = list(pharm_data.keys())  # ['18F', 'Ga']

    # Generator parameters for Ga
    # E.g. 1 time-step warmup, 1 time-step cooldown.
    WARMUP = 1
    COOLDOWN = 1

    def production_time_for_ga(dose_mbq):
        """Return discrete number of 5-min steps needed to produce 'dose_mbq' from the generator."""
        # For simplicity, say we need 1 time step per 100 MBq (rounded up).
        return math.ceil(dose_mbq / 100)

    # Precompute decay factors alpha_r^(delta) for delta in 0..Tmax
    # alpha_r = 2^(-5 / half_life_r)
    decay_factors = {}
    for r in radio_list:
        hl = pharm_data[r]['half_life']
        alpha = 2 ** (-5.0 / hl)
        # precompute alpha^(delta)
        decay_factors[r] = [alpha**d for d in range(Tmax+1)]

    # -----------------------------------------------------------
    # 2) Define the model's variables
    # -----------------------------------------------------------

    # 2.1 For each patient p, we define B[s,p]: boolean => "patient p starts injection at time s"
    B = {}
    for p in patients:
        for s in time_slots:
            B[p, s] = model.NewBoolVar(f"B_p{p}_s{s}")

    # We also define an IntVar S_p for "start injection time", but it will be linked to B.
    S_p = {}
    for p in patients:
        S_p[p] = model.NewIntVar(0, Tmax-1, f"S_{p}")

    # 2.2 For each external radio r != 'Ga', and each available slot t, quantity Q[r,t] purchased
    Q = {}
    big_M = 10000  # an upper bound on MBq purchased in one slot
    for r in radio_list:
        if r == 'Ga':
            continue  # Ga is produced by generator, not purchased
        for t in pharm_data[r]['available_slots']:
            Q[(r, t)] = model.NewIntVar(0, big_M, f"Q_{r}_t{t}")

    # 2.3 For each patient that needs Ga, define:
    #     - GaDose_p (how much we actually produce for that patient)
    #     - Generator interval GInterval_p with start, end
    #        including warmup + production + cooldown
    #     - AvailTime_p: when Ga is freshly produced (before decay)
    # Here we assume each Ga-using patient p gets exactly the dose they need in one batch.
    GaDose = {}
    Gstart = {}
    Gend = {}
    GenIntervals = []

    for p in patients:
        (u1, i1, u2, i2, neededDose, r) = patients_data[p]
        if r == 'Ga':
            # Production time depends on neededDose
            prod_time = production_time_for_ga(neededDose)
            total_gen_duration = WARMUP + prod_time + COOLDOWN

            Gstart[p] = model.NewIntVar(0, Tmax, f"Gstart_p{p}")
            Gend[p] = model.NewIntVar(0, Tmax, f"Gend_p{p}")
            ga_interval = model.NewIntervalVar(
                Gstart[p],
                total_gen_duration,
                Gend[p],
                f"GenInterval_p{p}"
            )
            GenIntervals.append(ga_interval)

            # The actual MBq we produce for patient p
            # (We allow the solver to choose how much to produce if we want to, or fix it).
            # Typically you'd set GaDose[p] = neededDose as an IntVar or just fix it to neededDose.
            # If there's an incentive to produce extra, you'd let it be flexible.
            GaDose[p] = model.NewIntVar(0, big_M, f"GaDose_p{p}")
            model.Add(GaDose[p] == neededDose)  # simplest approach: produce exactly what p needs

    # 2.4 Non-overlap of generator intervals for patients that need Ga
    # They share a single generator resource
    model.AddNoOverlap(GenIntervals)

    # 2.5 Scheduling the uptake/imaging on a single PET scanner
    # Just as in your original code, we define intervals for the imaging steps (1 and 2)
    intervals_imaging = []
    # We'll link them to S_p so that the uptake->imaging sequence is correct.
    for p in patients:
        (u1, i1, u2, i2, neededDose, r) = patients_data[p]

        # Start of imaging1
        start_imaging1 = model.NewIntVar(0, Tmax, f"start_im1_{p}")
        end_imaging1   = model.NewIntVar(0, Tmax, f"end_im1_{p}")
        model.Add(end_imaging1 == start_imaging1 + i1)

        im1 = model.NewIntervalVar(start_imaging1, i1, end_imaging1, f"im1_{p}")
        intervals_imaging.append(im1)

        # Imaging2
        start_imaging2 = model.NewIntVar(0, Tmax, f"start_im2_{p}")
        end_imaging2   = model.NewIntVar(0, Tmax, f"end_im2_{p}")
        model.Add(end_imaging2 == start_imaging2 + i2)

        im2 = model.NewIntervalVar(start_imaging2, i2, end_imaging2, f"im2_{p}")
        intervals_imaging.append(im2)

        # Link injection time S_p with imaging1 start:
        #   S_p + u1 = start_imaging1
        model.Add(start_imaging1 == S_p[p] + u1)
        # Link imaging2 to imaging1 end + uptake2
        model.Add(start_imaging2 == end_imaging1 + u2)

    # Single PET scanner => no overlap of imaging intervals
    model.AddNoOverlap(intervals_imaging)

    # -----------------------------------------------------------
    # 3) Constraints linking B[p,s] and S_p, plus feasibility
    # -----------------------------------------------------------

    # 3.1 Exactly one injection time s for each patient p
    for p in patients:
        model.Add(sum(B[p, s] for s in time_slots) == 1)

    # 3.2 S_p = sum_{s} s * B[p,s]
    for p in patients:
        model.Add(S_p[p] == sum(s * B[p, s] for s in time_slots))

    # 3.3 If patient p uses Ga, injection cannot happen before Ga is available.
    for p in patients:
        (u1, i1, u2, i2, neededDose, r) = patients_data[p]
        if r == 'Ga':
            # Suppose the activity is available right after warmup+production
            # AvailTime_p = Gstart[p] + WARMUP + production_time_for_ga(neededDose)
            prod_time_p = production_time_for_ga(neededDose)
            # We'll add S_p[p] >= AvailTime_p
            # i.e. S_p[p] >= Gstart[p] + WARMUP + prod_time_p
            model.Add(S_p[p] >= Gstart[p] + WARMUP + prod_time_p)

    # Define a scaling factor to convert float decay coefficients into integers.
    DECAY_SCALE = 1000
    M_big = 100000  # A suitably large constant, now an integer.

    # -----------------------------------------------------------
    # 4) Radioactive decay constraints
    #    We use a piecewise approach: If B[p,s] = 1, then we
    #    require enough decayed activity at time s to meet neededDose.
    # -----------------------------------------------------------
    for p in patients:
        (u1, i1, u2, i2, neededDose, r) = patients_data[p]
        # For each patient, enforce the decay constraint for each possible injection time s.
        if r != 'Ga':
            # For radio r purchased externally.
            for s in time_slots:
                decay_expr = []
                for t in pharm_data[r]['available_slots']:
                    if t <= s:
                        # Compute the decay factor for delay (s-t).
                        df = decay_factors[r][s - t]
                        # Scale df to an integer coefficient.
                        scaled_df = int(round(df * DECAY_SCALE))
                        # Instead of using .ScalProd(), use WeightedSum().
                        decay_expr.append(cp_model.LinearExpr.WeightedSum([Q[(r, t)]], [scaled_df]))
                total_decay_expr = cp_model.LinearExpr.Sum(decay_expr)
                # The right side must be scaled as well.
                model.Add(total_decay_expr + M_big * (1 - B[p, s]) >= neededDose * DECAY_SCALE)
        else:
            # For Ga produced by the generator.
            # Production time for patient p (in discrete steps)
            prod_time_p = production_time_for_ga(neededDose)
            # For each possible injection time s:
            for s in time_slots:
                # Earliest time Ga is available is after warmup and production.
                earliest_avail = WARMUP + prod_time_p
                if s >= earliest_avail:
                    delta_int = s - earliest_avail
                    df = decay_factors['Ga'][delta_int]
                else:
                    df = 0.0
                scaled_df = int(round(df * DECAY_SCALE))
                model.Add(GaDose[p] * scaled_df + M_big * (1 - B[p, s]) >= neededDose * DECAY_SCALE)

    # -----------------------------------------------------------
    # 5) Objective: Minimize total cost of purchased or produced radio
    # -----------------------------------------------------------
    # For externally purchased isotopes:
    # cost = sum_{r != Ga} sum_{t in A_r} (cost_r * Q[r,t]/1000)
    # For Ga:
    #  cost = sum_{p needing Ga} (cost_Ga * GaDose[p]/1000)
    #
    # In this example, we produce exactly the neededDose for Ga, so cost = costGa * neededDose/1000
    cost_terms = []

    for (r, t) in Q:
        cost_coef = pharm_data[r]['cost_per_GBq'] / 1000.0  # cost per MBq
        cost_terms.append((int(cost_coef*1000), Q[(r, t)]))

    # Ga cost
    ga_cost_coef = int(pharm_data['Ga']['cost_per_GBq'] / 1000.0 * 1000)
    for p in patients:
        (u1, i1, u2, i2, neededDose, r) = patients_data[p]
        if r == 'Ga':
            # GaDose[p] is an IntVar
            cost_terms.append((ga_cost_coef, GaDose[p]))

    model.Minimize(
        cp_model.LinearExpr.WeightedSum(
            [ct[1] for ct in cost_terms],
            [ct[0] for ct in cost_terms]
        )
    )

    # -----------------------------------------------------------
    # 6) Solve and Print
    # -----------------------------------------------------------
    solver = cp_model.CpSolver()
    # Optionally set a time limit or log search progress
    solver.parameters.max_time_in_seconds = 60.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print(f"Solution status: {solver.StatusName(status)}")
        print(f"Objective (scaled cost): {solver.ObjectiveValue()}")

        # Print injection times
        for p in patients:
            injection_time = solver.Value(S_p[p])
            (u1, i1, u2, i2, dose, r) = patients_data[p]
            print(f"\nPatient {p} uses {r}, needs {dose} MBq.")
            print(f"  Injection start (S_p) = {injection_time}")
            # Show which B[p,s] is 1
            for s in time_slots:
                if solver.Value(B[p,s]) == 1:
                    print(f"    B[p={p}, s={s}] = 1")

            # If Ga, print generator interval
            if r == 'Ga':
                gstart = solver.Value(Gstart[p])
                gend   = solver.Value(Gend[p])
                produce = solver.Value(GaDose[p])
                print(f"  Generator usage: start={gstart}, end={gend}, doseProduced={produce} MBq")
                print("  => warmup+prod ends at",
                      gstart + WARMUP + production_time_for_ga(dose),
                      "; then decays until injection time.")

        # Print how much external radio was purchased
        for (r, t) in Q:
            q_val = solver.Value(Q[(r,t)])
            if q_val > 0:
                print(f"Purchased {q_val} MBq of {r} at time {t}")

    else:
        print("No feasible solution found.")


if __name__ == "__main__":
    solve_pet_scheduling()
