from flask import Blueprint, render_template, request, redirect, url_for, flash
import uuid
from flask_login import login_required, current_user
from app.encrypt import get_fernet
from app.constants import (
    TESTS_TABLE,
    TEST_PATIENTS_TABLE,
    DOSING_SCHEMES_TABLE,
)
from app.table_manager import get_table_manager

tests_bp = Blueprint('tests', __name__, template_folder='templates')
fernet = get_fernet()


@login_required
@tests_bp.route('/tests', methods=['GET', 'POST'])
def manage_tests():
    user_id = current_user.username
    tm = get_table_manager()

    # POST → create new Test
    if request.method == 'POST':
        test_name = request.form.get('test_name', '').strip()
        if not test_name:
            flash("Test name is required.", "error")
            return redirect(url_for('tests.manage_tests'))

        new_test = {
            'PartitionKey': user_id,
            'RowKey': str(uuid.uuid4()),
            'Name': test_name
        }
        tm.upload_batch_to_table(TESTS_TABLE, [new_test])
        flash("Test created successfully.", "success")
        return redirect(url_for('tests.manage_tests'))

    # GET → list existing Tests
    tests = list(tm.query_entities(TESTS_TABLE, f"PartitionKey eq '{user_id}'"))
    tests.sort(key=lambda t: t.get('Name', '').lower())
    return render_template('tests.html', tests=tests)


@login_required
@tests_bp.route('/tests/edit/<row_key>', methods=['GET', 'POST'])
def edit_test(row_key):
    user_id = current_user.username
    tm = get_table_manager()
    test = tm.get_entity(TESTS_TABLE, user_id, row_key)
    if not test:
        flash("Test not found.", "error")
        return redirect(url_for('tests.manage_tests'))

    if request.method == 'POST':
        name = request.form.get('test_name', '').strip()
        if not name:
            flash("Test name is required.", "error")
            return redirect(url_for('tests.edit_test', row_key=row_key))

        test['Name'] = name
        tm.upload_batch_to_table(TESTS_TABLE, [test])
        flash("Test updated.", "success")
        return redirect(url_for('tests.manage_tests'))

    return render_template('edit_test.html', test=test)


@login_required
@tests_bp.route('/tests/delete/<row_key>', methods=['POST'])
def delete_test(row_key):
    user_id = current_user.username
    tm = get_table_manager()
    test = tm.get_entity(TESTS_TABLE, user_id, row_key)
    if test:
        tm.delete_entities(TESTS_TABLE, [test])
        # also delete its patients
        patients = list(tm.query_entities(
            TEST_PATIENTS_TABLE,
            f"PartitionKey eq '{user_id}' and TestID eq '{row_key}'"
        ))
        if patients:
            tm.delete_entities(TEST_PATIENTS_TABLE, patients)
        flash("Test and its patients deleted.", "success")
    else:
        flash("Test not found.", "error")
    return redirect(url_for('tests.manage_tests'))


@login_required
@tests_bp.route('/tests/clear', methods=['POST'])
def clear_tests():
    user_id = current_user.username
    tm = get_table_manager()
    all_tests = list(tm.query_entities(TESTS_TABLE, f"PartitionKey eq '{user_id}'"))
    if all_tests:
        tm.delete_entities(TESTS_TABLE, all_tests)
        # clear all test-patients
        all_patients = list(tm.query_entities(TEST_PATIENTS_TABLE, f"PartitionKey eq '{user_id}'"))
        if all_patients:
            tm.delete_entities(TEST_PATIENTS_TABLE, all_patients)
        flash("All tests (and their patients) cleared.", "success")
    else:
        flash("No tests to clear.", "info")
    return redirect(url_for('tests.manage_tests'))


#
# -- Per-Test patient management (same fields & dosing logic) --
#

@login_required
@tests_bp.route('/tests/<test_id>/patients', methods=['GET', 'POST'])
def manage_test_patients(test_id):
    user_id = current_user.username
    tm = get_table_manager()

    # ensure Test exists
    test = tm.get_entity(TESTS_TABLE, user_id, test_id)
    print(test)
    if not test:
        flash("Test not found.", "error")
        return redirect(url_for('tests.manage_tests'))

    # common dosing-scheme lookup
    dosing_schemes = list(tm.query_entities(DOSING_SCHEMES_TABLE, f"PartitionKey eq '{user_id}'"))
    scheme_by_key = {s['RowKey']: s for s in dosing_schemes}
    available_rads = sorted({s['Radiopharmaceutical'] for s in dosing_schemes if s.get('Radiopharmaceutical')})

    if request.method == 'POST':
        # add patient to this Test
        surname = request.form['surname']
        given  = request.form['given_name']
        ident  = request.form['identification']
        try:
            weight = float(request.form['weight'])
        except:
            flash("Weight must be a number.", "error")
            return redirect(url_for('tests.manage_test_patients', test_id=test_id))

        scheme_id = request.form['dosing_scheme']
        scheme = scheme_by_key.get(scheme_id)
        if not scheme:
            flash("Scheme not found.", "error")
            return redirect(url_for('tests.manage_test_patients', test_id=test_id))

        dose_val = float(scheme.get('DoseValue', 0))
        dose_type = scheme.get('DoseType', 'fixed')
        administered = (dose_val * weight) if dose_type=='per_kg' else dose_val

        pe = lambda s: fernet.encrypt(s.encode()).decode()
        patient = {
            'PartitionKey': user_id,
            'RowKey': str(uuid.uuid4()),
            'TestID': test_id,
            'Surname': pe(surname),
            'GivenName': pe(given),
            'Identification': pe(ident),
            'Weight': weight,
            'DosingSchemeID': scheme_id,
            'AdministeredDose': administered
        }
        tm.upload_batch_to_table(TEST_PATIENTS_TABLE, [patient])
        flash("Patient added to test.", "success")
        return redirect(url_for('tests.manage_test_patients', test_id=test_id))

    # GET → list patients for this Test
    patients = list(tm.query_entities(
        TEST_PATIENTS_TABLE,
        f"PartitionKey eq '{user_id}' and TestID eq '{test_id}'"
    ))
    # decrypt
    for p in patients:
        try:
            p['Surname']        = fernet.decrypt(p['Surname'].encode()).decode()
            p['GivenName']      = fernet.decrypt(p['GivenName'].encode()).decode()
            p['Identification'] = fernet.decrypt(p['Identification'].encode()).decode()
        except:
            p['Surname'] = p['GivenName'] = p['Identification'] = "Decryption Error"

    dosing_schemes.sort(key=lambda s: s['Name'])
    return render_template(
        'test_patients.html',
        test=test,
        patients=patients,
        dosing_schemes=dosing_schemes,
        available_radiopharmaceuticals=available_rads,
        dosing_scheme_by_rowkey=scheme_by_key
    )


@login_required
@tests_bp.route('/tests/<test_id>/patients/edit/<row_key>', methods=['GET', 'POST'])
def edit_test_patient(test_id, row_key):
    user_id = current_user.username
    tm = get_table_manager()
    test = tm.get_entity(TESTS_TABLE, user_id, test_id)
    patient = tm.get_entity(TEST_PATIENTS_TABLE, user_id, row_key)
    if not test or not patient or patient.get('TestID') != test_id:
        flash("Invalid test or patient.", "error")
        return redirect(url_for('tests.manage_tests'))

    # decrypt for display
    try:
        patient['Surname']        = fernet.decrypt(patient['Surname'].encode()).decode()
        patient['GivenName']      = fernet.decrypt(patient['GivenName'].encode()).decode()
        patient['Identification'] = fernet.decrypt(patient['Identification'].encode()).decode()
    except:
        flash("Error decrypting patient.", "error")
        return redirect(url_for('tests.manage_test_patients', test_id=test_id))

    # common dosing schemes
    dosing_schemes = list(tm.query_entities(DOSING_SCHEMES_TABLE, f"PartitionKey eq '{user_id}'"))
    available_rads = sorted({s['Radiopharmaceutical'] for s in dosing_schemes if s.get('Radiopharmaceutical')})
    dosing_schemes.sort(key=lambda s: s['Name'])
    current_scheme = tm.get_entity(DOSING_SCHEMES_TABLE, user_id, patient['DosingSchemeID'])
    current_rad = current_scheme.get('Radiopharmaceutical') if current_scheme else ''

    if request.method == 'POST':
        # process update
        surname = request.form['surname']
        given   = request.form['given_name']
        ident   = request.form['identification']
        try:
            weight = float(request.form['weight'])
        except:
            flash("Weight must be a number.", "error")
            return redirect(url_for('tests.edit_test_patient', test_id=test_id, row_key=row_key))

        scheme_id = request.form['dosing_scheme']
        scheme = tm.get_entity(DOSING_SCHEMES_TABLE, user_id, scheme_id)
        if not scheme:
            flash("Scheme not found.", "error")
            return redirect(url_for('tests.edit_test_patient', test_id=test_id, row_key=row_key))

        dose_val  = float(scheme.get('DoseValue', 0))
        dose_type = scheme.get('DoseType', 'fixed')
        administered = (dose_val * weight) if dose_type=='per_kg' else dose_val

        pe = lambda s: fernet.encrypt(s.encode()).decode()
        patient.update({
            'Surname':        pe(surname),
            'GivenName':      pe(given),
            'Identification': pe(ident),
            'Weight':        weight,
            'DosingSchemeID': scheme_id,
            'AdministeredDose': administered
        })
        tm.upload_batch_to_table(TEST_PATIENTS_TABLE, [patient])
        flash("Patient updated.", "success")
        return redirect(url_for('tests.manage_test_patients', test_id=test_id))

    return render_template(
        'edit_test_patient.html',
        test=test,
        patient=patient,
        dosing_schemes=dosing_schemes,
        available_radiopharmaceuticals=available_rads,
        current_radiopharmaceutical=current_rad
    )


@login_required
@tests_bp.route('/tests/<test_id>/patients/delete/<row_key>', methods=['POST'])
def delete_test_patient(test_id, row_key):
    user_id = current_user.username
    tm = get_table_manager()
    patient = tm.get_entity(TEST_PATIENTS_TABLE, user_id, row_key)
    if patient and patient.get('TestID') == test_id:
        tm.delete_entities(TEST_PATIENTS_TABLE, [patient])
        flash("Patient removed.", "success")
    else:
        flash("Patient not found.", "error")
    return redirect(url_for('tests.manage_test_patients', test_id=test_id))


@login_required
@tests_bp.route('/tests/<test_id>/patients/clear', methods=['POST'])
def clear_test_patients(test_id):
    user_id = current_user.username
    tm = get_table_manager()
    patients = list(tm.query_entities(
        TEST_PATIENTS_TABLE,
        f"PartitionKey eq '{user_id}' and TestID eq '{test_id}'"
    ))
    if patients:
        tm.delete_entities(TEST_PATIENTS_TABLE, patients)
        flash("All patients cleared from test.", "success")
    else:
        flash("No patients to clear.", "info")
    return redirect(url_for('tests.manage_test_patients', test_id=test_id))
