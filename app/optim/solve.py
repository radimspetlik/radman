#! /usr/bin/env python3

import pyomo.environ as pyo
from math import log2

if __name__ == "__main__":
    #
    # === PROBLEM DATA (small example) ===
    #
    time_horizon = 12  # 12 discrete time steps, each 5 min => 60 min total

    P = [1,2]  # patients
    F = ['Ga68','F18']  # pharmaceuticals

    # half-life in minutes
    half_life = {
        'Ga68': 68.0,
        'F18' : 109.8
    }

    # cost (per GBq)
    cost = {
        'Ga68': 300.0,  # placeholder cost (EUR / GBq, for example)
        'F18' : 100.0
    }

    # Which pharmaceutical each patient needs
    phi = {1: 'Ga68',
           2: 'F18'}

    # Dose requirement (MBq) for each patient
    d_p = {
        1: 100.0,  # patient1 needs 100 MBq
        2: 250.0   # patient2 needs 250 MBq
    }

    # Uptake / Imaging durations (in discrete steps)
    # Example: 1 step = 5 min
    u1 = {1: 1, 2: 2}  # uptake1 durations
    i1 = {1: 1, 2: 1}
    u2 = {1: 1, 2: 1}
    i2 = {1: 1, 2: 1}

    # Let's say each pharma is "available" to be purchased
    # at all time steps for simplicity. For a real case,
    # you'd specify a subset of times. For 68Ga we also have
    # generator constraints below.
    A = {
        'Ga68': list(range(time_horizon)),  # can produce at any time step
        'F18' : list(range(time_horizon))
    }

    # 68Ga generator constraints (example)
    # We'll say: if we produce Ga68 at time t, it occupies that time step
    # plus a warm-up of 1 step before and a cooldown of 1 step after
    gen_warm = 1
    gen_cool = 1

    #
    # === BUILDING THE MODEL ===
    #
    model = pyo.ConcreteModel("PET_Scheduling")

    # --- Sets ---
    model.T = pyo.RangeSet(0,time_horizon-1)
    model.P = pyo.Set(initialize=P)
    model.F = pyo.Set(initialize=F)

    # --- Decision Variables ---

    # 1) S_{p,t}: 1 if patient p starts at time t, else 0
    model.S = pyo.Var(model.P, model.T, domain=pyo.Binary)

    # 2) x_{f,t}: amount (MBq) of pharmaceutical f purchased at time t
    model.x = pyo.Var(model.F, model.T, domain=pyo.NonNegativeReals)

    # 3) generator production indicator: y_{t} = 1 if Ga68 is produced at time t
    #    (to handle warm-up/cooldown)
    model.yGa = pyo.Var(model.T, domain=pyo.Binary)

    # --- Objective: Minimize total cost ---
    # Need to convert MBq -> GBq:  1 GBq = 1000 MBq
    def total_cost_rule(m):
        return sum(cost[f] * (m.x[f,t]/1000.0) for f in F for t in m.T)
    model.TotalCost = pyo.Objective(rule=total_cost_rule, sense=pyo.minimize)

    #
    # --- Constraints ---
    #

    # (A) Each patient starts exactly once.
    def patient_starts_once_rule(m, p):
        return sum(m.S[p,t] for t in m.T) == 1
    model.OneStart = pyo.Constraint(model.P, rule=patient_starts_once_rule)

    # (B) No scanner overlap: for each pair (p,q) with p<q,
    #     and each pair of start times (t1, t2), we forbid
    #     overlap in their imaging intervals.
    def no_overlap_rule(m, p, q, t1, t2):
        if p == q:
            return pyo.Constraint.Skip
        # Compute intervals for p if it starts at t1
        # Imaging1: [t1 + u1_p, t1 + u1_p + i1_p)
        # Imaging2: [t1 + u1_p + i1_p + u2_p, t1 + u1_p + i1_p + u2_p + i2_p)
        # Similarly for q if starts at t2
        p_im1_start = t1 + u1[p]
        p_im1_end   = t1 + u1[p] + i1[p]
        p_im2_start = t1 + u1[p] + i1[p] + u2[p]
        p_im2_end   = t1 + u1[p] + i1[p] + u2[p] + i2[p]

        q_im1_start = t2 + u1[q]
        q_im1_end   = t2 + u1[q] + i1[q]
        q_im2_start = t2 + u1[q] + i1[q] + u2[q]
        q_im2_end   = t2 + u1[q] + i1[q] + u2[q] + i2[q]

        # We say they do NOT overlap if
        #   p_im2_end <= q_im1_start or q_im2_end <= p_im1_start  (but we need the complement for overlap)
        # We'll forbid overlap, so if S[p,t1] & S[q,t2] =1 => no overlap.
        # A simpler approach: big-M style: If they both start, that implies
        # p's intervals happen either entirely before q's or entirely after.
        # We'll do a single linear inequality approach:
        # If both start, then p's imaging must finish before q's imaging begins or vice versa.
        # This is typically something like:
        # (p_im2_end <= q_im1_start) or (q_im2_end <= p_im1_start).
        # We can represent these as:
        #    p_im2_end - q_im1_start <= M * (1 - S[p,t1] - S[q,t2])
        #    q_im2_end - p_im1_start <= M * (1 - S[p,t1] - S[q,t2])
        # but for a small horizon, we can just skip constraints that obviously can't happen.

        # check if intervals could overlap in principle
        # if there's no possible overlap, skip
        # if there IS potential overlap, impose that S[p,t1] + S[q,t2] <= 1
        # for simplicity:
        # We'll do an explicit overlap check:
        # Overlap can occur if p_im1_start < q_im2_end and q_im1_start < p_im2_end
        # If that is possible, we add: S[p,t1] + S[q,t2] <= 1

        # find earliest imaging start time and latest imaging end time for each
        p_im_start = p_im1_start
        p_im_end   = p_im2_end
        q_im_start = q_im1_start
        q_im_end   = q_im2_end

        # If the intervals [p_im_start, p_im_end) and [q_im_start, q_im_end) can overlap,
        # we add the constraint that they can't both happen.
        if p_im_start < q_im_end and q_im_start < p_im_end:
            return m.S[p,t1] + m.S[q,t2] <= 1
        else:
            return pyo.Constraint.Skip

    model.NoOverlap = pyo.Constraint(model.P, model.P, model.T, model.T, rule=no_overlap_rule)

    # (C) Dose supply: If patient p starts at time t, we need
    #     sum_{tau=0..t} x_{phi(p),tau} * 2^{-((t-tau)*5 / half_life(phi(p)))} >= d_p
    def dose_supply_rule(m, p, t):
        # half-life
        hl = half_life[ phi[p] ]
        decay_factor = []
        # sum of decayed amounts
        decayed_sum = sum(
            m.x[ phi[p], tau ] * 2**( - ( (t - tau)*5.0 / hl ) )
            for tau in range(t+1)
        )
        return decayed_sum >= d_p[p] * m.S[p,t]
    model.DoseSupply = pyo.Constraint(model.P, model.T, rule=dose_supply_rule)

    # (D) Ga68 generator resource constraints:
    #     If yGa[t] = 1 => we can produce x['Ga68',t] > 0
    #     and if yGa[t] = 1, then yGa[t-1], yGa[t+1], etc. must be 0
    #     for warm-up/cooldown.  We'll do a simpler version:
    #       yGa[t] => x['Ga68',t] >= 0, no direct upper limit
    #       if yGa[t] = 1, then yGa[t-1] = 0, yGa[t+1] = 0, etc.
    #       for 1 step each side, to mimic warm/cool.
    def generator_link_rule(m, t):
        # if we do not produce at t, then x['Ga68',t] = 0
        return m.x['Ga68',t] <= 9999 * m.yGa[t]  # big M
    model.GaLink = pyo.Constraint(model.T, rule=generator_link_rule)

    # Warm-up/cooldown:
    def generator_cooldown_rule(m, t):
        # if yGa[t] = 1, then yGa[t-1] = yGa[t+1] = 0 if in range
        # We'll do piecewise constraints for t in 0..time_horizon-1
        cons = []
        if t - gen_warm >= 0:
            return m.yGa[t] + m.yGa[t-1] <= 1
        else:
            return pyo.Constraint.Skip

    model.GaCoolDownA = pyo.Constraint(model.T, rule=generator_cooldown_rule)

    # You would similarly add for t+gen_cool if in range, etc.
    # For brevity, here's a minimal pattern:
    def generator_cooldown_rule2(m, t):
        if t + gen_cool < time_horizon:
            return m.yGa[t] + m.yGa[t+1] <= 1
        else:
            return pyo.Constraint.Skip
    model.GaCoolDownB = pyo.Constraint(model.T, rule=generator_cooldown_rule2)

    #
    # === Solve ===
    #
    solver = pyo.SolverFactory('cbc')  # or 'glpk', ...
    result = solver.solve(model, tee=True)

    #
    # === Print Results ===
    #
    print("Status:", result.solver.status)
    print("Termination Condition:", result.solver.termination_condition)

    print("\nOptimal cost:", pyo.value(model.TotalCost))
    print("\nPurchase plan (x_{f,t}):")
    for f in model.F:
        for t in model.T:
            val = pyo.value(model.x[f,t])
            if val > 1e-6:
                print(f"  Purchase {val:.2f} MBq of {f} at time {t}")

    print("\nGenerator usage (yGa[t]):")
    for t in model.T:
        if pyo.value(model.yGa[t]) > 0.5:
            print(f"  Generate Ga68 at time {t}")

    print("\nPatient scheduling S_{p,t}:")
    for p in model.P:
        for t in model.T:
            if pyo.value(model.S[p,t]) > 0.5:
                print(f"  Patient {p} starts at time {t}")
