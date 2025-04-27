from flask import Blueprint, render_template, redirect, url_for, flash
import math
import datetime
from flask_login import login_required, current_user

from app.constants import PATIENTS_TABLE, DOSING_SCHEMES_TABLE, TEST_PATIENTS_TABLE
from app.encrypt import get_fernet
from app.solve_ga import cbc
from app.table_manager import get_table_manager

optim_bp = Blueprint('optim', __name__, template_folder='templates')
fernet = get_fernet()

@login_required
@optim_bp.route('/optim', methods=['GET'])
@optim_bp.route('/optim/<string:test_id>', methods=['GET'])
def do_it(test_id=None):
    user_id = current_user.username
    table_manager = get_table_manager()

    result, ga68_generator, pharma_inventory = cbc(test_id)
    if result is None:
        flash("No solution found for the given constraints.", "error")

    # Query all patients for the current user.
    if test_id:
        # If a test ID is provided, filter patients by that test.
        patients_list = list(
            table_manager.query_entities(TEST_PATIENTS_TABLE, f"PartitionKey eq '{user_id}' and TestID eq '{test_id}'")
        )
    else:
        patients_list = list(
            table_manager.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{user_id}'")
        )

    # Create the schedule header: times from 06:00 to 17:00 in 10-minute increments.
    time_slots = []
    base_time = datetime.datetime(2025, 1, 1, 6, 0)  # dummy date to generate time strings
    total_slots = 66  # (11 hours * 6 slots per hour)
    for i in range(total_slots):
        time_slots.append((base_time + datetime.timedelta(minutes=i * 10)).strftime("%H:%M"))

    patients_with_timeline = []
    for patient in patients_list:
        # Decrypt patient data.
        try:
            patient["Surname"] = fernet.decrypt(patient["Surname"].encode()).decode()
            patient["GivenName"] = fernet.decrypt(patient["GivenName"].encode()).decode()
            patient["Identification"] = fernet.decrypt(patient["Identification"].encode()).decode()
        except Exception as e:
            # Mark fields as unavailable if decryption fails.
            patient["Surname"] = "Decryption Error"
            patient["GivenName"] = "Decryption Error"
            patient["Identification"] = "Decryption Error"

        timeline = [""] * total_slots
        current_slot = 0
        if result:
            # Retrieve the patient's assigned time slot from the result.
            patient_id = patient.get("RowKey")
            if patient_id in result:
                current_slot = result[patient_id] // 10  # Convert to slot index (10-minute intervals)

        # Retrieve the dosing scheme record for this patient.
        dosing_scheme_id = patient.get("DosingSchemeID")
        dosing_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, dosing_scheme_id)
        if dosing_scheme:
            try:
                uptake1 = int(dosing_scheme.get("Uptake1", 0))
                imaging1 = int(dosing_scheme.get("Imaging1", 0))
                uptake2 = int(dosing_scheme.get("Uptake2", 0))
                imaging2 = int(dosing_scheme.get("Imaging2", 0))
            except (ValueError, TypeError):
                uptake1 = imaging1 = uptake2 = imaging2 = 0
        else:
            uptake1 = imaging1 = uptake2 = imaging2 = 0

        def mark_block(label, duration):
            nonlocal current_slot
            blocks = math.ceil(duration / 10) if duration > 0 else 0
            for _ in range(blocks):
                if current_slot < total_slots:
                    timeline[current_slot] = label
                    current_slot += 1

        # Mark timeline blocks in sequence.
        mark_block("U1", uptake1)
        mark_block("I1", imaging1)
        mark_block("U2", uptake2)
        mark_block("I2", imaging2)
        patient["timeline"] = timeline

        # Compute procedure start time: first non-empty timeline cell.
        start_time = ""
        for i, cell in enumerate(timeline):
            if cell:
                start_time = time_slots[i]
                break
        patient["start_time"] = start_time

        # Store dosing scheme display details.
        if dosing_scheme:
            patient["Radiopharmaceutical"] = dosing_scheme.get("Radiopharmaceutical", "N/A")
            patient["Scheme"] = dosing_scheme.get("Name", "N/A")
        else:
            patient["Radiopharmaceutical"] = "N/A"
            patient["Scheme"] = "N/A"

        patients_with_timeline.append(patient)

    # -- Build an additional timeline row for the 68Ga generator --
    # This row uses the ga68_generator tuples where each tuple is (start_time_str, duration_minutes).
    generator_timeline = [""] * total_slots
    if ga68_generator:
        for gen_tuple in ga68_generator:
            gen_start, gen_duration = gen_tuple  # e.g., ("6:00", 20)
            try:
                gen_index = time_slots.index(gen_start)
            except ValueError:
                gen_index = 0  # Fallback if the start time is not found in time_slots.
            gen_blocks = math.ceil(gen_duration / 10) if gen_duration > 0 else 0
            for _ in range(gen_blocks):
                if gen_index < total_slots:
                    generator_timeline[gen_index] = "68Ga"
                    gen_index += 1

    # change pharma_inventory levels to 10 minute intervals from 5 minute intervals
    corrected_pharma_inventory = []
    for pharma_name, pharma_purchases, pharma_level in pharma_inventory:
        pharma_purchases_corrected = []
        pharma_level_corrected = []
        for i in range(0, len(pharma_level), 2):
            pharma_purchases_corrected.append(pharma_purchases[i] + pharma_purchases[i + 1])
            pharma_level_corrected.append(pharma_level[i])
        # fill the remaining to total_slots
        while len(pharma_purchases_corrected) < total_slots:
            pharma_purchases_corrected.append(0)
            pharma_level_corrected.append(0)
        corrected_pharma_inventory.append((pharma_name, pharma_purchases_corrected, pharma_level_corrected))


    # Render the template with time_slots and patients.
    return render_template("optim.html", time_slots=time_slots, patients=patients_with_timeline,
                           generator_timeline=generator_timeline, pharma_inventory=corrected_pharma_inventory)
