from flask import Blueprint, render_template, request, redirect, url_for, flash
import uuid
from flask_login import login_required, current_user
from app.encrypt import get_fernet
from app.constants import DOSING_SCHEMES_TABLE, PATIENTS_TABLE
from app.table_manager import get_table_manager

patients_bp = Blueprint('patients', __name__, template_folder='templates')
fernet = get_fernet()


@login_required
@patients_bp.route('/patients', methods=['GET', 'POST'])
def manage_patients():
    user_id = current_user.username
    table_manager = get_table_manager()

    # Retrieve available dosing schemes for the current user.
    dosing_schemes = list(
        table_manager.query_entities(DOSING_SCHEMES_TABLE, f"PartitionKey eq '{user_id}'")
    )
    dosing_scheme_by_rowkey = {scheme['RowKey']: scheme for scheme in dosing_schemes}

    # Compute unique radiopharmaceuticals (those that are associated with a dosing scheme).
    available_rads = sorted(
        list({scheme.get('Radiopharmaceutical') for scheme in dosing_schemes if scheme.get('Radiopharmaceutical')})
    )

    if request.method == 'POST':
        # Add a new patient.
        surname = request.form.get('surname')
        given_name = request.form.get('given_name')
        identification = request.form.get('identification')
        try:
            weight = float(request.form.get('weight'))
        except (ValueError, TypeError):
            flash("Weight must be a number.", "error")
            return redirect(url_for('patients.manage_patients'))

        # Retrieve the dosing scheme (selected from the filtered list).
        dosing_scheme_id = request.form.get('dosing_scheme')  # should be the RowKey

        # Retrieve the selected dosing scheme record.
        dosing_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, dosing_scheme_id)
        if not dosing_scheme:
            flash("Selected dosing scheme not found.", "error")
            return redirect(url_for('patients.manage_patients'))

        # Convert the dosing scheme dose value to a float.
        try:
            dose_value = float(dosing_scheme.get('DoseValue', 0))
        except (ValueError, TypeError):
            dose_value = 0

        dose_type = dosing_scheme.get('DoseType', 'fixed')
        if dose_type == 'per_kg':
            administered_dose = dose_value * weight
        else:
            administered_dose = dose_value

        # Encrypt the personal data fields.
        encrypted_surname = fernet.encrypt(surname.encode()).decode()
        encrypted_given_name = fernet.encrypt(given_name.encode()).decode()
        encrypted_identification = fernet.encrypt(identification.encode()).decode()

        # Build the patient record.
        patient_entity = {
            'PartitionKey': user_id,
            'RowKey': str(uuid.uuid4()),
            'Surname': encrypted_surname,
            'GivenName': encrypted_given_name,
            'Identification': encrypted_identification,
            'Weight': weight,
            'DosingSchemeID': dosing_scheme_id,
            'AdministeredDose': administered_dose
        }
        table_manager.upload_batch_to_table(PATIENTS_TABLE, [patient_entity])
        flash("Patient added successfully.", "success")
        return redirect(url_for('patients.manage_patients'))

    # GET request: retrieve already added patients.
    patients_list = list(
        table_manager.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{user_id}'")
    )

    # Decrypt personal data before sending to the template.
    for patient in patients_list:
        try:
            patient["Surname"] = fernet.decrypt(patient["Surname"].encode()).decode()
            patient["GivenName"] = fernet.decrypt(patient["GivenName"].encode()).decode()
            patient["Identification"] = fernet.decrypt(patient["Identification"].encode()).decode()
        except Exception as e:
            patient["Surname"] = "Decryption Error"
            patient["GivenName"] = "Decryption Error"
            patient["Identification"] = "Decryption Error"

    dosing_schemes = sorted(dosing_schemes, key=lambda x: x['Name'])
    return render_template("patients.html",
                           dosing_schemes=dosing_schemes,
                           available_radiopharmaceuticals=available_rads,
                           patients=patients_list,
                           dosing_scheme_by_rowkey=dosing_scheme_by_rowkey)


@login_required
@patients_bp.route('/patients/edit/<row_key>', methods=['GET', 'POST'])
def edit_patient(row_key):
    user_id = current_user.username
    table_manager = get_table_manager()

    # Retrieve the patient record.
    patient = table_manager.get_entity(PATIENTS_TABLE, user_id, row_key)
    if not patient:
        flash("Patient not found.", "error")
        return redirect(url_for('patients.manage_patients'))

    # Decrypt the patient data.
    try:
        patient["Surname"] = fernet.decrypt(patient["Surname"].encode()).decode()
        patient["GivenName"] = fernet.decrypt(patient["GivenName"].encode()).decode()
        patient["Identification"] = fernet.decrypt(patient["Identification"].encode()).decode()
    except Exception as e:
        flash("Error decrypting patient data.", "error")
        return redirect(url_for('patients.manage_patients'))

    # Retrieve available dosing schemes for the dropdown.
    dosing_schemes = list(
        table_manager.query_entities(DOSING_SCHEMES_TABLE, f"PartitionKey eq '{user_id}'")
    )
    # Compute unique radiopharmaceuticals.
    available_rads = sorted(
        list({scheme.get('Radiopharmaceutical') for scheme in dosing_schemes if scheme.get('Radiopharmaceutical')})
    )
    dosing_schemes = sorted(dosing_schemes, key=lambda x: x['Name'])
    # Determine the radiopharmaceutical corresponding to the patient’s dosing scheme.
    current_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, patient.get('DosingSchemeID'))
    current_rad = current_scheme.get('Radiopharmaceutical') if current_scheme else ""

    if request.method == 'POST':
        # Process the update.
        surname = request.form.get('surname')
        given_name = request.form.get('given_name')
        identification = request.form.get('identification')
        try:
            weight = float(request.form.get('weight'))
        except (ValueError, TypeError):
            flash("Weight must be a number.", "error")
            return redirect(url_for('patients.edit_patient', row_key=row_key))

        dosing_scheme_id = request.form.get('dosing_scheme')
        dosing_scheme = table_manager.get_entity(DOSING_SCHEMES_TABLE, user_id, dosing_scheme_id)
        if not dosing_scheme:
            flash("Selected dosing scheme not found.", "error")
            return redirect(url_for('patients.edit_patient', row_key=row_key))

        try:
            dose_value = float(dosing_scheme.get('DoseValue', 0))
        except (ValueError, TypeError):
            dose_value = 0

        dose_type = dosing_scheme.get('DoseType', 'fixed')
        if dose_type == 'per_kg':
            administered_dose = dose_value * weight
        else:
            administered_dose = dose_value

        # Encrypt updated personal data.
        encrypted_surname = fernet.encrypt(surname.encode()).decode()
        encrypted_given_name = fernet.encrypt(given_name.encode()).decode()
        encrypted_identification = fernet.encrypt(identification.encode()).decode()

        # Update the patient record.
        patient['Surname'] = encrypted_surname
        patient['GivenName'] = encrypted_given_name
        patient['Identification'] = encrypted_identification
        patient['Weight'] = weight
        patient['DosingSchemeID'] = dosing_scheme_id
        patient['AdministeredDose'] = administered_dose

        table_manager.upload_batch_to_table(PATIENTS_TABLE, [patient])
        flash("Patient updated successfully.", "success")
        return redirect(url_for('patients.manage_patients'))

    return render_template("edit_patient.html", patient=patient,
                           dosing_schemes=dosing_schemes,
                           available_radiopharmaceuticals=available_rads,
                           current_radiopharmaceutical=current_rad)


@login_required
@patients_bp.route('/patients/delete/<row_key>', methods=['POST'])
def delete_patient(row_key):
    user_id = current_user.username
    table_manager = get_table_manager()
    patient = table_manager.get_entity(PATIENTS_TABLE, user_id, row_key)
    if patient:
        table_manager.delete_entities(PATIENTS_TABLE, [patient])
        flash("Patient deleted successfully.", "success")
    else:
        flash("Patient not found.", "error")
    return redirect(url_for('patients.manage_patients'))


@login_required
@patients_bp.route('/patients/clear', methods=['POST'])
def clear_patients():
    user_id = current_user.username
    table_manager = get_table_manager()
    patients_to_clear = list(
        table_manager.query_entities(PATIENTS_TABLE, f"PartitionKey eq '{user_id}'")
    )
    if patients_to_clear:
        table_manager.delete_entities(PATIENTS_TABLE, patients_to_clear)
        flash("All patients cleared.", "success")
    else:
        flash("No patients to clear.", "info")
    return redirect(url_for('patients.manage_patients'))
