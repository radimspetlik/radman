from flask import Blueprint
import math
import datetime
from flask import render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from app.constants import PATIENTS_TABLE, DOSING_SCHEMES_TABLE
from app.encrypt import get_fernet
from app.table_manager import get_table_manager

optim_bp = Blueprint('optim', __name__, template_folder='templates')

fernet = get_fernet()


@login_required
@optim_bp.route('/optim', methods=['GET'])
def do_it():
    user_id = current_user.username
    table_manager = get_table_manager()

    # Query all patients for the current user.
    patients_list = list(
        table_manager.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{user_id}'")
    )

    # Create the schedule header: times from 06:00 to 17:00 in 10 minute increments.
    time_slots = []
    base_time = datetime.datetime(2025, 1, 1, 6, 0)  # dummy date to generate time strings
    total_slots = 66  # (11 hours * 6 slots per hour)
    for i in range(total_slots):
        time_slots.append((base_time + datetime.timedelta(minutes=i * 10)).strftime("%H:%M"))

    # For each patient, look up the dosing scheme and compute a timeline.
    # The timeline will be a list of 66 cells. For each dosing block (uptake1, imaging1, uptake2, imaging2),
    # if its duration (in minutes) is > 0, compute the number of 10-minute blocks (using math.ceil),
    # and fill the timeline sequentially starting at index 0.
    patients_with_timeline = []
    for patient in patients_list:

        try:
            patient["Surname"] = fernet.decrypt(patient["Surname"].encode()).decode()
            patient["GivenName"] = fernet.decrypt(patient["GivenName"].encode()).decode()
            patient["Identification"] = fernet.decrypt(patient["Identification"].encode()).decode()
        except Exception as e:
            # Optionally log the error; here we mark fields as unavailable.
            patient["Surname"] = "Decryption Error"
            patient["GivenName"] = "Decryption Error"
            patient["Identification"] = "Decryption Error"

        timeline = [""] * total_slots
        current_slot = 0

        # Retrieve the dosing scheme record for this patient.
        dosing_scheme_id = patient.get("DosingSchemeID")
        dosing_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, dosing_scheme_id)
        if dosing_scheme:
            # Get block durations (in minutes) for each phase.
            try:
                uptake1 = int(dosing_scheme.get("Uptake1", 0))
                imaging1 = int(dosing_scheme.get("Imaging1", 0))
                uptake2 = int(dosing_scheme.get("Uptake2", 0))
                imaging2 = int(dosing_scheme.get("Imaging2", 0))
            except (ValueError, TypeError):
                uptake1 = imaging1 = uptake2 = imaging2 = 0
        else:
            uptake1 = imaging1 = uptake2 = imaging2 = 0

        # Define a helper function to mark timeline blocks.
        def mark_block(label, duration):
            nonlocal current_slot
            blocks = math.ceil(duration / 10) if duration > 0 else 0
            for _ in range(blocks):
                if current_slot < total_slots:
                    timeline[current_slot] = label
                    current_slot += 1

        # Mark each block in order.
        mark_block("U1", uptake1)
        mark_block("I1", imaging1)
        mark_block("U2", uptake2)
        mark_block("I2", imaging2)

        # Attach the computed timeline to the patient record.
        patient["timeline"] = timeline
        patients_with_timeline.append(patient)

    return render_template("optim.html", time_slots=time_slots, patients=patients_with_timeline)

