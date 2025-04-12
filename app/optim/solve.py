from ortools.sat.python import cp_model
import math

def solve_pet_scheduling():
    model = cp_model.CpModel()

    # -------------------------
    # 1) Define data/instances
    # -------------------------
    # Time is in discrete steps of 5 minutes: T = {0..Tmax-1}
    Tmax = 20  # e.g. up to 20 discrete slots => 20*5min=100min
    time_slots = range(Tmax)

    # Example patients
    # We store: (UptakeDur1, ImagingDur1, UptakeDur2, ImagingDur2, DoseMBq, pharmRequired)
    patients_data = {
        0: (1, 2, 1, 2, 300, '18F'),  # needs 300 MBq, uses 18F
        1: (1, 2, 1, 2, 250, 'Ga'),   # needs 250 MBq, uses Ga
        # Add more patients here as needed
    }
    patients = list(patients_data.keys())

    # Radiopharmaceuticals
    # For each: (half_life_minutes, cost_per_GBq, available_time_slots)
    #  - For Ga we might treat it specially (see below).
    #  - For others, we can purchase at certain time slots with no limit.
    pharm_data = {
        '18F': {
            'half_life': 109.8,
            'cost_per_GBq': 100.0,  # example cost
            'available_slots': [0, 4, 8],  # can be purchased at t=0,4,8
        },
        'Ga': {
            'half_life': 68.0,    # 68 minutes
            'cost_per_GBq': 200.0 # example cost, or handle generator differently
            # We'll treat availability via generator constraints
        }
    }
    radio_list = list(pharm_data.keys())  # ['18F', 'Ga']

    # For discrete decay, precompute alpha^(s-t) for each radio, to keep it simpler
    # alpha_{r} = 2^(-(5 minutes) / half_life_r)
    # but we just store alpha_{r}^{delta} for delta in [0..Tmax]
    decay_factors = {}
    for r in radio_list:
        half_life = pharm_data[r]['half_life']
        alpha = 2 ** (-5.0 / half_life)  # decay factor per 5-min step
        # precompute alpha^(delta)
        decay_factors[r] = [alpha**delta for delta in range(Tmax+1)]

    # --------------------------------------
    # 2) Define the model's decision variables
    # --------------------------------------

    # 2.1 Scheduling variables: S_p = start time for patient's Uptake1
    S_p = {}
    for p in patients:
        S_p[p] = model.NewIntVar(0, Tmax-1, f"S_{p}")

    # 2.2 Purchase quantity variables for non-Ga pharmaceuticals
    #     Q_{r,t} = amount of r purchased at time t (in MBq)
    Q = {}
    big_M = 10000  # upper bound on MBq to buy in any slot
    for r in radio_list:
        if r == 'Ga':
            continue  # we'll do Ga with separate variables or logic
        for t in pharm_data[r]['available_slots']:
            Q[r, t] = model.NewIntVar(0, big_M, f"Q_{r}_t{t}")

    # 2.3 (Optional) Ga generation variables
    # For simplicity, let's let QGa[t] = how much Ga we produce at time t
    # ignoring generator warmup/cooldown. In a real model, you’d have intervals.
    QGa = {}
    for t in time_slots:
        QGa[t] = model.NewIntVar(0, big_M, f"QGa_t{t}")

    # 2.4 Imaging intervals in CP-SAT: We'll define intervals for imaging only.
    #     For each patient p, create intervals for imaging1 and imaging2.
    intervals_imaging = []
    for p in patients:
        (u1, i1, u2, i2, dose, r) = patients_data[p]
        start_imaging1 = model.NewIntVar(0, Tmax, f"start_im1_{p}")
        start_imaging2 = model.NewIntVar(0, Tmax, f"start_im2_{p}")

        # The imaging durations
        dur_im1 = i1
        dur_im2 = i2

        # Create end variables for imaging intervals
        end_imaging1 = model.NewIntVar(0, Tmax, f"end_im1_{p}")
        end_imaging2 = model.NewIntVar(0, Tmax, f"end_im2_{p}")

        # Add constraints that link start, duration, and end:
        model.Add(end_imaging1 == start_imaging1 + dur_im1)
        model.Add(end_imaging2 == start_imaging2 + dur_im2)

        # IntervalVar for imaging1
        im1 = model.NewIntervalVar(start_imaging1,
                                   dur_im1,
                                   end_imaging1,
                                   f"im1_{p}")
        # IntervalVar for imaging2
        im2 = model.NewIntervalVar(start_imaging2,
                                   dur_im2,
                                   end_imaging2,
                                   f"im2_{p}")
        intervals_imaging.append(im1)
        intervals_imaging.append(im2)

        # Link S_p (start of uptake1) to start_imaging1:
        #   start_imaging1 = S_p + u1
        model.Add(start_imaging1 == S_p[p] + u1)
        # And for imaging2:
        #   start_imaging2 = start_imaging1 + i1 + u2
        model.Add(start_imaging2 == start_imaging1 + i1 + u2)

    # 2.5 Non-overlap of imaging intervals on single PET scanner:
    model.AddNoOverlap(intervals_imaging)

    # --------------------------------------
    # 3) Dose availability constraints
    # --------------------------------------
    # Each patient p must have enough pharm at time S_p[p] (the “injection” time).
    # Summation of all purchases/productions up to S_p, decayed to S_p, >= DoseMBq[p].
    #
    # We'll do an approximation: we require that the total "radio" in the
    # discrete times 0..(S_p[p]) is enough, considering the discrete decay factor.

    for p in patients:
        (u1, i1, u2, i2, neededDose, r) = patients_data[p]

        # Build an expression for total available at time S_p
        # sum_{t=0..S_p} [Q_{r,t} * decay_factors[r][S_p - t]]
        # For Ga, we sum QGa[t], for 18F we sum Q[18F,t], etc.
        avail_expr = []
        # We have to use a piecewise approach because S_p is itself a variable.
        # Typical solution: For each t in [0..Tmax], we create a piecewise expression
        # that gets "activated" if S_p == s. Then we sum decayed amounts up to s.
        # Because we are in CP-SAT, we can do it with the "element" approach or
        # reify constraints. For demonstration, we'll do a simpler big-M method
        # that ensures for all t <= S_p, we have enough. (This can be over-constraining
        # but simpler for demonstration.)

        # We'll enforce: For each t < s, that part is included. For t >= s, we ignore.
        # So: We'll create a boolean indicator "Used[t,p]" = 1 if t <= S_p[p], else 0.
        # Then the sum of used[t,p] is S_p[p]+1 if we do direct equality. We do a less
        # refined approach: "t <= S_p" => t - S_p[p] <= 0 => typical reification approach.

        # 3.1 Create booleans for t <= S_p[p].
        Used = {}
        for t in time_slots:
            Used[t] = model.NewBoolVar(f"Used_{t}_p{p}")
            # The constraint "t <= S_p[p]" can be modeled as:
            #    t <= S_p[p]   <=>   t - S_p[p] <= 0
            # We reify that with big-M:
            #    t - S_p[p] <= 0 + M*(1-Used[t])
            #    S_p[p] - t <= M*(Used[t])
            M_bound = Tmax
            model.Add(t - S_p[p] <= 0 + M_bound*(1 - Used[t]))
            model.Add(S_p[p] - t <= M_bound*(Used[t]))

        # 3.2 Now define an availability expression using these booleans:
        # For each t, quantity Q_{r,t} decayed to time S_p is Q_{r,t} * alpha^(S_p - t).
        # We'll approximate by forcing a lower bound that, for each t, we assume worst-case
        # or we sum over all s. In a pure CP approach, we could do a piecewise approach with
        # an array of "element" constraints. Here for demonstration, let's do:
        #   sum_{t=0..Tmax-1} [ Q_{r,t} * alpha^(Tmax-1 - t) * Used[t] ] >= neededDose
        # This ensures that if S_p >= t, we get the maximum possible decay time to
        # the worst-case S_p. This is quite rough but is a workable demonstration.
        # A more precise approach would be to do a decomposition with "IfThen(S_p==s)" constraints.

        if r != 'Ga':
            # For example, we sum Q[r,t]* alpha^(Tmax-1 - t)*Used[t]
            # to get a safe lower bound of the available quantity at time S_p.
            decayed_sum = []
            for t in pharm_data[r]['available_slots']:
                # We'll use alpha^(Tmax-1 - t) as if the injection time were (Tmax-1).
                # This is conservative (i.e. we assume maximum decay).
                df = decay_factors[r][Tmax-1 - t]
                decayed_sum.append(model.NewIntVar(0, big_M*1000, f"decayPart_{r}_{t}_p{p}"))
                # We need to create an integer expression = Q[r,t] * df * Used[t].
                # Because Q[r,t] is an IntVar and df is a constant < 1, we can't directly multiply
                # an IntVar by a fractional constant. Typically, we scale up. For demonstration,
                # we'll do a big-M approach or use the floating linear extension of CP-SAT
                # (in OR-Tools 9.4+). Let's do the floating approach here for brevity:
                pass

            # Because CP-SAT primarily uses integer arithmetic, a truly correct approach
            # to decay requires a small integer approximation trick. We'll skip it here
            # and just demonstrate a "big-M bounding" approach below.
        else:
            # Ga
            pass

        # Minimal demonstration approach:
        # We'll simply add a constraint that sum_{t=0..Tmax-1} QGa[t] + sum_{other r} >= neededDose
        # This obviously ignores time/decay in a formal sense, but shows the structure.
        # You can refine as above.
        # ------------
        if r == 'Ga':
            # We assume all Ga is generated "just in time", ignoring real generator constraints
            # for demonstration:
            model.Add(sum(QGa[t] for t in time_slots) >= neededDose)
        else:
            # Summation of all purchased 18F (for example) ignoring decay:
            model.Add(sum(Q[r, t] for t in pharm_data[r]['available_slots']) >= neededDose)

    # --------------------------------------
    # 4) Objective: Minimize total cost
    # --------------------------------------
    # total_cost = sum_{r != Ga} sum_{t in A_r} (cost_r * Q[r,t]/1000)
    #            + sum_{t} (cost_Ga * QGa[t]/1000)
    # We'll do a float expression in CP-SAT. We wrap them as a WeightedSum approach.
    cost_terms = []
    for (r, t) in Q:
        cost_coef = pharm_data[r]['cost_per_GBq'] / 1000.0  # cost per MBq
        # WeightedSum: cost_coef * Q[r,t]
        # But Q[r,t] is an IntVar. We can create a linear expression. CP-SAT can handle that.
        cost_terms.append((int(cost_coef*1000), Q[r,t]))  # scale up to keep integer

    # Add Ga cost
    ga_cost_coef = int(pharm_data['Ga']['cost_per_GBq'] / 1000.0 * 1000)
    for t in time_slots:
        cost_terms.append((ga_cost_coef, QGa[t]))

    model.Minimize(
        cp_model.LinearExpr.WeightedSum([ct[1] for ct in cost_terms],
                                        [ct[0] for ct in cost_terms])
    )

    # --------------------------------------
    # 5) Solve
    # --------------------------------------
    solver = cp_model.CpSolver()
    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        print(f"Solution status: {solver.StatusName(status)}")
        print(f"Objective (scaled cost): {solver.ObjectiveValue()}")
        # If you want the cost in real numeric form, scale down again.

        # Print schedule
        for p in patients:
            start_val = solver.Value(S_p[p])
            (u1, i1, u2, i2, dose, r) = patients_data[p]
            print(f"Patient {p}: start time = {start_val}")
            print(f"  pharm = {r}, dose = {dose} MBq")

        # Print purchase amounts
        for (r, t) in Q:
            q_val = solver.Value(Q[r, t])
            if q_val > 0:
                print(f"Purchase {q_val} MBq of {r} at time {t}")

        # Print Ga production
        for t in time_slots:
            q_val = solver.Value(QGa[t])
            if q_val > 0:
                print(f"Generate {q_val} MBq of Ga at time {t}")

    else:
        print("No feasible solution found.")

if __name__ == "__main__":
    solve_pet_scheduling()