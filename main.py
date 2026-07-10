try:
	from runtime.core.pipeline import Pipeline

	pipeline = Pipeline(
		credentials_path=PATH_CREDENTIALS,
		output_dir=PATH_OUTPUTS,
	)
	
	# Nur ein Benutzer, letzte 2 Tage
	pipeline.run_forever(initial_limit=200, initial_max_age_days=1, user_mail="info@strausskopie.at")
except Exception as e:
	print(e)
	input()