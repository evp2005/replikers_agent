import environ

env = environ.Env()
environ.Env.read_env()

PROJECT_ID = env('PROJECT_ID', default=None)
REGION = env('REGION', default=None)
STAGING_BUCKET = env('STAGING_BUCKET', default=None)
GOOGLE_APPLICATION_CREDENTIALS = env('GOOGLE_APPLICATION_CREDENTIALS', default=None)
GOOGLE_DRIVE_CREDENTIALS = env('GOOGLE_DRIVE_CREDENTIALS', default=None)