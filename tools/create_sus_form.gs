/**
 * Creates the usability questionnaire (SUS + explanation clarity) as a Google Form.
 *
 * Paste into https://script.google.com (New project), save, choose
 * createSusForm in the toolbar and press Run. The execution log then shows:
 *   - the EDIT link (for you),
 *   - the PARTICIPANT link,
 *   - the PRE-FILLED link with P00 in the code field -> SUS_FORM_PREFILL_URL.
 *
 * Question titles are what app.eval.study_results matches on (by prefix):
 * "Consent", "Participant code", "Your main role at work",
 * "How long have you used an ERP or business system?", "Q1." .. "Q10.", "C1." .. "C5.".
 * Q1-Q10 are the standard System Usability Scale items (Brooke, 1996),
 * with "this system" kept as in the original.
 */
function createSusForm() {
  var form = FormApp.create('ClarityDQ – usability questionnaire');
  form.setDescription(
    'Thank you for trying ClarityDQ. This short questionnaire takes about 4 minutes. ' +
    'Your answers are anonymous: we only use your participant code, never your name. ' +
    'They will be used in a university research project.');
  form.setCollectEmail(false);
  form.setProgressBar(true);
  form.setConfirmationMessage('Thank you! Your answers have been recorded. You can close this page.');

  // --- Page 1: consent (No -> the form ends) ---------------------------------
  var consent = form.addMultipleChoiceItem()
    .setTitle('Consent: I have read the information above and agree to take part')
    .setRequired(true);
  consent.setChoices([
    consent.createChoice('Yes, I agree', FormApp.PageNavigationType.CONTINUE),
    consent.createChoice('No', FormApp.PageNavigationType.SUBMIT)
  ]);

  // --- Page 2: about you -----------------------------------------------------
  form.addPageBreakItem().setTitle('About you');
  var code = form.addTextItem()
    .setTitle('Participant code')
    .setHelpText('The code you used in the app, e.g. P01 (it may already be filled in).')
    .setRequired(true);
  code.setValidation(FormApp.createTextValidation()
    .requireTextMatchesPattern('^[Pp][0-9]{2}$')
    .setHelpText('Please enter your code, e.g. P01.')
    .build());
  form.addMultipleChoiceItem()
    .setTitle('Your main role at work')
    .setChoiceValues(['Accountant / finance', 'Storekeeper / inventory', 'Data-entry operator',
                      'Manager / business owner', 'IT / technical', 'Student'])
    .showOtherOption(true)
    .setRequired(true);
  form.addMultipleChoiceItem()
    .setTitle('How long have you used an ERP or business system?')
    .setChoiceValues(['Never', 'Less than 1 year', '1–3 years', '3–5 years', 'More than 5 years'])
    .setRequired(true);

  // --- Page 3: SUS (standard 10 items) ----------------------------------------
  form.addPageBreakItem().setTitle('Using the system')
    .setHelpText('For each statement, choose how much you agree (1 = strongly disagree, 5 = strongly agree).');
  var sus = [
    'I think that I would like to use this system frequently.',
    'I found the system unnecessarily complex.',
    'I thought the system was easy to use.',
    'I think that I would need the support of a technical person to be able to use this system.',
    'I found the various functions in this system were well integrated.',
    'I thought there was too much inconsistency in this system.',
    'I would imagine that most people would learn to use this system very quickly.',
    'I found the system very cumbersome to use.',
    'I felt very confident using the system.',
    'I needed to learn a lot of things before I could get going with this system.'
  ];
  for (var i = 0; i < sus.length; i++) {
    form.addScaleItem().setTitle('Q' + (i + 1) + '. ' + sus[i])
      .setBounds(1, 5).setLabels('Strongly disagree', 'Strongly agree').setRequired(true);
  }

  // --- Page 4: explanation clarity -------------------------------------------
  form.addPageBreakItem().setTitle('The explanations')
    .setHelpText('About how the system explained the data problems (1 = strongly disagree, 5 = strongly agree).');
  var clarity = [
    'The explanation of each issue was easy to understand.',
    'I understood why the system flagged the issue.',
    'The suggested fix ("What to do") was clear and useful.',
    'The chat answer helped me understand the issue better.',
    'The report was clear enough to share with a manager.'
  ];
  for (var j = 0; j < clarity.length; j++) {
    form.addScaleItem().setTitle('C' + (j + 1) + '. ' + clarity[j])
      .setBounds(1, 5).setLabels('Strongly disagree', 'Strongly agree').setRequired(true);
  }

  // --- Page 5: open questions (optional) -------------------------------------
  form.addPageBreakItem().setTitle('Your comments');
  form.addParagraphTextItem().setTitle('What was the most useful part of the system?');
  form.addParagraphTextItem().setTitle('What was the most confusing part?');
  form.addParagraphTextItem().setTitle('If you could change one thing, what would it be?');
  form.addParagraphTextItem().setTitle('Any other comments?');

  // --- Links -----------------------------------------------------------------
  var prefilled = form.createResponse()
    .withItemResponse(code.createResponse('P00'))
    .toPrefilledUrl();
  Logger.log('EDIT link (for you):        ' + form.getEditUrl());
  Logger.log('PARTICIPANT link:           ' + form.getPublishedUrl());
  Logger.log('PRE-FILLED link (P00) -> SUS_FORM_PREFILL_URL:');
  Logger.log(prefilled);
}
