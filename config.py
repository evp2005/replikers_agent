import environ

env = environ.Env()
environ.Env.read_env()

PROJECT_ID = env('PROJECT_ID', default=None)
REGION = env('REGION', default=None)
STAGING_BUCKET = env('STAGING_BUCKET', default=None)

PATH_SA_AGENTE = './sa-agente.json'
PATH_SA_GDRIVE = './sa-gdrive.json'