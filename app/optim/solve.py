#!/usr/bin/env python3

from ortools.linear_solver import pywraplp
import math

def solve_pet_scheduling():
    # --- 1) DATA DEFINITION ---

    # Time is discretized in 5-min increments.
    # Let T_max = 15 time slots => total horizon is 15 * 5 = 75 min (toy example).
    T_max = 15
    time_slots = range(T_max+1)  # 0..15

    # Radiopharmaceuticals:
    # R = { 'F18', 'Ga68' } for demonstration. Each has:
    #   - cost [$/GBq]
    #   - half_life [minutes]
    #   - availability slots (where we can purchase/generate)
    # We can add more isotopes (11C, 15O, etc.) similarly.
    R = ['F18', 'Ga68']
    cost = {
        'F18': 3000.0,    # say $3000 per GBq
        'Ga68': 1000.0,   # cost "accounting" for generator usage or hypothetical cost
    }
    half_life = {
        'F18': 109.8,
        'Ga68': 68.0
    }
    # Suppose external F18 is available at timeslots 0, 5, 10 only.
    # Suppose Ga68 can be generated at any timeslot  (toy assumption).
    availability = {
        'F18': [0, 5, 10],
        'Ga68': list(time_slots)
    }

    # Precompute decay factor from t' to t for each r, t' < t
    # d[r][t'][t] = fraction of radioisotope r purchased at t' that remains at t
    # if t >= t' else 0
    d = {}
    for r in R:
        lam = math.log(2) / half_life[r]
        d[r] = [[0.0]*(T_max+1) for _ in range(T_max+1)]
        for tprime in time_slots:
            for t in time_slots:
                if t >= tprime:
                    delta_minutes = (t - tprime)*5
                    d[r][tprime][t] = math.exp(-lam * delta_minutes)
                else:
                    d[r][tprime][t] = 0.0

    # Patients (toy example):
    # Let there be 2 patients, each requiring two blocks of uptake+imaging.
    # For each block, we know:
    #   (uptakeDuration, imagingDuration, requiredMBq, which pharma)
    # The second block must start after the first block finishes for that patient.

    # We represent them as: patients[p] = [ (uptake1, imaging1, dose1, r),
    #                                       (uptake2, imaging2, dose2, r) ]
    patients = {
        0: [(1, 2, 200.0, 'F18'),   # block1 for patient0
            (1, 2, 250.0, 'F18')],  # block2 for patient0
        1: [(2, 2, 150.0, 'Ga68'),
            (2, 2, 180.0, 'Ga68')]
    }
    P = list(patients.keys())

    # --- 2) CREATE SOLVER ---
    solver = pywraplp.Solver.CreateSolver('CBC')
    if not solver:
        print("Could not create CBC solver.")
        return

    # --- 3) DEFINE DECISION VARIABLES ---

    # x[r, t] >= 0 : amount of r purchased at time t (in MBq).
    x = {}
    for r in R:
        for t in time_slots:
            x[r, t] = solver.NumVar(0, solver.infinity(), f'x_{r}_{t}')

    # Start_{p, b, t} in {0,1} : 1 if block b of patient p starts at time t
    Start = {}
    for p in P:
        for b in [0,1]:
            for t in time_slots:
                Start[(p,b,t)] = solver.IntVar(0, 1, f'Start_{p}_{b}_{t}')

    # --- 4) CONSTRAINTS ---

    # 4.1 Purchase availability => x[r,t] = 0 if t not in availability[r]
    for r in R:
        for t in time_slots:
            if t not in availability[r]:
                solver.Add(x[r,t] == 0)

    # 4.2 Each block must start exactly once
    for p in P:
        for b in [0,1]:
            solver.Add(
                sum(Start[(p,b,t)] for t in time_slots) == 1
            )

    # 4.3 Sequencing within each patient: block b finishes before b+1 starts
    # block b finish time = start time + uptakeDuration + imagingDuration
    # Let S_{p,b} = sum(t * Start_{p,b,t}).
    # We'll build it via a helper expression:
    def block_start_expr(p, b):
        return sum(t * Start[(p,b,t)] for t in time_slots)

    for p in P:
        for b in [0]:
            uptake_b   = patients[p][b][0]
            imaging_b  = patients[p][b][1]
            # Next block is b+1
            uptake_b1  = patients[p][b+1][0]
            imaging_b1 = patients[p][b+1][1]
            solver.Add(
                block_start_expr(p,b) + uptake_b + imaging_b
                <= block_start_expr(p,b+1)
            )

    # 4.4 No overlap on PET scanner
    # For each time slot t, at most one block can be in its imaging phase.
    # A block b for patient p is in imaging at time slot k if
    # k in [S_{p,b} + uptake_b, ..., S_{p,b} + uptake_b + imaging_b - 1].
    # We'll do a direct big-M approach with a binary test for "imaging at t" for each block,
    # then sum over all p,b must be <= 1.

    # Introduce a helper boolean: InImaging_{p,b,t}, 1 if block b of patient p is imaging at slot t.
    # We can define large M or directly link them. For simplicity here, we define constraints that
    # force InImaging_{p,b,t} to 1 only if t is within the correct imaging window for that block's start.
    InImaging = {}
    for p in P:
        for b in [0,1]:
            uptake_b  = patients[p][b][0]
            imaging_b = patients[p][b][1]
            for t in time_slots:
                InImaging[(p,b,t)] = solver.IntVar(0, 1, f'Imaging_{p}_{b}_{t}')
                # Link to Start_{p,b,t0}:
                # t is in imaging window if there exists t0 with Start_{p,b,t0} = 1 and
                # t in [t0 + uptake_b, ..., t0 + uptake_b + imaging_b - 1].
                # We'll do: InImaging_{p,b,t} >= Start_{p,b,t0} if t0 + uptake_b <= t <= t0 + uptake_b + imaging_b - 1
                # Then also InImaging_{p,b,t} <= sum of all relevant Start_{p,b,t0}.
                c1 = []
                for t0 in time_slots:
                    if t0 + uptake_b <= t <= t0 + uptake_b + imaging_b - 1:
                        c1.append(Start[(p,b,t0)])
                # InImaging_{p,b,t} <= sum(c1)
                if c1:
                    solver.Add(InImaging[(p,b,t)] <= sum(c1))
                else:
                    # No possible start time can cause imaging at t => must be 0
                    solver.Add(InImaging[(p,b,t)] == 0)

    # Now: sum_{p,b} InImaging_{p,b,t} <= 1 for each t
    for t in time_slots:
        solver.Add(
            sum(InImaging[(p,b,t)] for p in P for b in [0,1]) <= 1
        )

    # 4.5 Dose requirement constraints
    # For block b of patient p, injection occurs at S_{p,b}, i.e. time slot of start.
    # That means we must have enough decayed activity of the needed pharma r_p,b
    # to cover dose_{p,b}.
    # Let demanded = patients[p][b][2]
    # Let pharma = patients[p][b][3]

    for p in P:
        for b in [0,1]:
            demanded = patients[p][b][2]
            rpb = patients[p][b][3]   # required radiopharma
            # Demand must be satisfied EXACTLY at S_{p,b}. In discrete form,
            # we require for each t: if S_{p,b} == t => sum_{t'<=t} x[rpb,t'] * d[rpb][t'][t] >= demanded.

            # We'll create a big-M style constraint that enforces:
            #   sum_{t'<=Tmax} x[rpb, t'] * d[rpb][t'][t] >= demanded * Start_{p,b,t}
            # for each t in time_slots.
            for t in time_slots:
                # Left side is total decayed activity at time t
                decayed_sum = sum( x[rpb, tprime] * d[rpb][tprime][t]
                                   for tprime in range(t+1) )
                solver.Add(
                    decayed_sum >= demanded * Start[(p,b,t)]
                )

    # --- 5) OBJECTIVE: Minimize cost = sum_{r,t} cost_r * x[r,t]/1000
    objective = solver.Sum(
        (cost[r]/1000.0) * x[r,t]
        for r in R
        for t in time_slots
    )
    solver.Minimize(objective)

    # --- 6) Solve ---
    status = solver.Solve()
    if status == pywraplp.Solver.OPTIMAL:
        print("Optimal solution found with total cost =", solver.Objective().Value())
        # Print the schedule
        for p in P:
            for b in [0,1]:
                for t in time_slots:
                    if Start[(p,b,t)].solution_value() > 0.5:
                        uptake_b  = patients[p][b][0]
                        imaging_b = patients[p][b][1]
                        print(f"Patient {p}, block {b} starts at t={t} (uptake {uptake_b} slots) => imaging at [{t+uptake_b}, {t+uptake_b+imaging_b-1}]")
        # Print purchase amounts
        for r in R:
            for t in time_slots:
                val = x[r,t].solution_value()
                if val > 1e-6:
                    print(f"Purchase {val:.2f} MBq of {r} at t={t}")
    else:
        print("No optimal solution found. Status =", status)

if __name__ == '__main__':
    solve_pet_scheduling()
