import vertexai
import os
from config import PROJECT_ID, REGION, STAGING_BUCKET, PATH_SA_AGENTE, PATH_SA_GDRIVE
# Configura las credenciales desde el archivo JSON (opcional si ya configuraste la variable de entorno)

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = PATH_SA_AGENTE 
# os.environ["GOOGLE_DRIVE_CREDENTIALS"] = str(GOOGLE_DRIVE_CREDENTIALS)  
# os.environ["PROJECT_ID"] = PROJECT_ID 
# os.environ["REGION"] = REGION 
# os.environ["STAGING_BUCKET"] = STAGING_BUCKET

# PROJECT_ID = PROJECT_ID # @param {type:"string"}
# REGION = REGION  # @param {type: "string"}
# STAGING_BUCKET = STAGING_BUCKET

# Inicializar Vertex AI
vertexai.init(project=PROJECT_ID, location=REGION, staging_bucket=STAGING_BUCKET)

print(f"Your project ID is: {PROJECT_ID}")

from typing import Optional
from typing import Annotated, Literal, TypedDict, Dict

from langchain_core.messages import HumanMessage
from langchain_google_vertexai import ChatVertexAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph, MessagesState
from langgraph.constants import START, END
from langgraph.prebuilt import ToolNode
from langchain_core.prompts import PromptTemplate
from langchain_community.document_loaders import PyPDFLoader
import tempfile
import os
import io
# from vertexai.preview import reasoning_engines
# import PyPDF2
# from typing import BinaryIO


from langchain.tools import tool, StructuredTool
from googleapiclient.discovery import build
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google.cloud import storage
from langchain.schema import AIMessage
# from google.cloud import vision

def should_continue(state: MessagesState) -> Literal["tools", END]:
    """
    Determina si el flujo debe continuar llamando a herramientas o finalizar la interacción.

    Args:
        state (MessagesState): Estado actual del flujo de mensajes.

    Returns:
        Literal["tools", END]: "tools" si se deben usar herramientas; END si el flujo debe finalizar.
    """
    messages = state['messages']
    if not messages:
        return END  # No hay mensajes, no hay nada que hacer

    last_message = messages[-1]

    # Verificar si el último mensaje incluye llamadas a herramientas
    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "tools"  # Continuar con herramientas

    return END

class MultiAgentLangGraphApp:
    def __init__(self, project: str, location: str) -> None:
        self.project_id = project
        self.location = location

        # Define the tools for the agent to use
        @tool
        def analyze_pdfs_from_bucket(bucket_name: str, pdf_name: str = None) -> str:
            """
            Analiza uno o varios PDFs en un bucket de Google Cloud Storage.

            Args:
                bucket_name (str): Nombre del bucket.
                pdf_name (str, opcional): Nombre del archivo PDF específico. Si no se proporciona, analiza todos los PDFs en el bucket.

            Returns:
                str: Contenido extraído de los PDFs.
            """
            try:
                storage_client = storage.Client()
                bucket = storage_client.bucket(bucket_name)

                if pdf_name:
                    # Analizar un archivo específico
                    blob = bucket.blob(pdf_name)
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                        blob.download_to_file(temp_file)
                        temp_file_path = temp_file.name

                    loader = PyPDFLoader(temp_file_path)
                    pages = loader.load()
                    full_text = "\n".join([page.page_content for page in pages])

                    os.unlink(temp_file_path)
                    return f"Contenido del PDF '{pdf_name}':\n{full_text}"

                else:
                    # Analizar todos los PDFs en el bucket
                    blobs = bucket.list_blobs()
                    pdf_contexts = []

                    for blob in blobs:
                        if blob.name.lower().endswith('.pdf'):
                            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
                                blob.download_to_file(temp_file)
                                temp_file_path = temp_file.name

                            loader = PyPDFLoader(temp_file_path)
                            pages = loader.load()
                            full_text = "\n".join([page.page_content for page in pages])

                            pdf_contexts.append(f"PDF: {blob.name}\n{full_text}")
                            os.unlink(temp_file_path)

                    return "\n\n---\n\n".join(pdf_contexts)

            except Exception as e:
                return f"Error al analizar PDFs: {str(e)}"

        # Configura el alcance y las credenciales
        # scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # credencials = GOOGLE_DRIVE_CREDENTIALS
        # creds = ServiceAccountCredentials.from_json_keyfile_dict(credencials, scope)
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(PATH_SA_GDRIVE, scope)
        client = gspread.authorize(creds)

        @tool
        def crear_documento_google_drive(title: str) -> str:
            """
            Crea un documento vacío en Google Drive y devuelve su enlace para compartir.
            """
            try:
                service = build('docs', 'v1', credentials=creds)
                document = {'title': title}
                doc = service.documents().create(body=document).execute()
                self.document_id = doc.get('documentId')

                drive_service = build('drive', 'v3', credentials=creds)
                drive_service.permissions().create(
                    fileId=self.document_id,
                    body={'role': 'writer', 'type': 'anyone'}
                ).execute()

                return f"Documento creado exitosamente: https://docs.google.com/document/d/{self.document_id}/edit"
            except Exception as e:
                return f"Error al crear el documento en Google Drive: {str(e)}"

        @tool
        def actualizar_documento_google_drive(content: str) -> str:
            """
            Actualiza el documento de Google Drive con nuevo contenido, aplicando formato apropiado.

            Args:
                content (str): Contenido a insertar en el documento.

            Returns:
                str: Mensaje de éxito o error.
            """
            try:
                if not content.strip():
                    return "Error: El contenido proporcionado está vacío."

                if not self.document_id:
                    return "Error: No se ha creado un documento aún para actualizar."

                service = build('docs', 'v1', credentials=creds)

                # Limpiar el contenido y prepararlo para el formato
                content = content.replace('\\n', '\n')  # Reemplazar caracteres de escape
                sections = content.split('---')  # Dividir por secciones

                requests = []
                current_index = 1  # Comenzamos en 1 para dejar espacio para el título

                for section in sections:
                    if not section.strip():
                        continue

                    # Insertar el texto de la sección
                    requests.append({
                        'insertText': {
                            'location': {'index': current_index},
                            'text': section.strip() + '\n\n'
                        }
                    })

                    # Obtener el texto insertado
                    text_length = len(section.strip() + '\n\n')

                    # Aplicar formato al título de la sección (si existe)
                    if '**' in section:
                        title_start = section.find('**') + current_index
                        title_end = section.find('**', title_start) + current_index

                        requests.append({
                            'updateParagraphStyle': {
                                'range': {
                                    'startIndex': title_start,
                                    'endIndex': title_end
                                },
                                'paragraphStyle': {
                                    'namedStyleType': 'HEADING_1',
                                    'spaceAbove': {'magnitude': 10, 'unit': 'PT'},
                                    'spaceBelow': {'magnitude': 10, 'unit': 'PT'}
                                },
                                'fields': 'namedStyleType,spaceAbove,spaceBelow'
                            }
                        })

                    # Actualizar el índice actual
                    current_index += text_length

                # Ejecutar todas las actualizaciones
                service.documents().batchUpdate(
                    documentId=self.document_id,
                    body={'requests': requests}
                ).execute()

                return "Documento actualizado exitosamente con formato aplicado."
            except Exception as e:
                print(f"Error al actualizar el documento en Google Drive: {e}")
                return f"Error al actualizar el documento: {str(e)}"

        # (should_continue) Define the function that determines whether to continue or not
        # Definir herramientas como StructuredTools
        self.tools = [analyze_pdfs_from_bucket, crear_documento_google_drive, actualizar_documento_google_drive]
        self.tool_node = ToolNode(self.tools)

        self.model = ChatVertexAI(
            model="gemini-1.5-pro-001",
            temperature=0,
            project=self.project_id
        ).bind_tools(self.tools)

        # Modelo configurado con herramientas
        self.prompt_template = PromptTemplate(
                    input_variables=["query"],
                    template="""
                    **IMPORTANTE:** Todo este proceso debe ser interno, y el usuario no debe visualizarlo.
                    ### Instrucciones:
                    #### 1. **Análisis de PDFs:**
                    - Examina los PDFs proporcionados en el bucket (utiliza la herramienta **analyze_pdfs_from_bucket** para acceder y extraer la información.)
                      para identificar cómo están estructurados y modelados los CVs. Esto incluye su jerarquía de datos, formato y elementos clave.
                    - Utiliza la estructura y formato de los PDFs como modelos para crear un CV profesional a medida para el usuario.

                    #### 2. **Creación del Modelo de CV: Instrucciones para la Creación del CV**
                    #### Paso 1: Determinar el flujo
                    - Si el usuario esta listo para crear su CV:
                        - Haz preguntas estructuradas al usuario para recopilar la información necesaria.
                        - Actualiza el documento en cada paso usando `actualizar_documento_google_drive`.

                    #### Paso 2: Usar modelos de ejemplo
                    - Usa `analyze_pdfs_from_bucket` para analizar ejemplos de CVs almacenados en el bucket.
                    - Asegúrate de aplicar buenas prácticas de diseño y formato de CVs basándote en estos modelos.

                    #### Paso 3: Guía interactiva para recopilar información
                    - Haz preguntas claras y específicas, como:
                        - "¿Cuál es tu nombre completo?"
                        - "¿Qué experiencia laboral relevante te gustaría incluir?", confirma que el usuario tenga mas de una experiencia Laboral.
                        - "¿Qué habilidades o logros quieres destacar?"
                    - Organiza las respuestas en secciones del CV: Información personal, Objetivo profesional, Experiencia laboral, Educación, Habilidades y Referencias.

                    #### Paso 4: Generar el CV
                    - Al finalizar, utiliza `crear_documento_google_drive` si no se ha creado previamente.
                    - Asegúrate de que el documento tenga el siguiente formato:
                    ---
                    **[NOMBRE COMPLETO]**
                    [Teléfono] | [Ciudad, País] | [Correo electrónico] | [Perfil de LinkedIn]
                    **RESUMEN PROFESIONAL**
                    [Descripción breve y concisa sobre tu perfil profesional. Por ejemplo: "Profesional con más de [X] años de experiencia en [campo/industria]. Especialista en [habilidades clave]. Reconocido por [logros destacables]".]
                    ---
                    **EXPERIENCIA PROFESIONAL**
                    **[Título del puesto]**
                    [Nombre de la empresa] • [Ciudad, País] • [Mes/Año de inicio] – [Mes/Año de fin o "Actual"]

                    - [Responsabilidad principal o descripción del rol. Ejemplo: "Lideré estrategias para incrementar la rentabilidad de [X]%."]
                    - [Logro medible o relevante. Ejemplo: "Incrementé las ventas en [X]% mediante [método]."]
                    - [Logro adicional o acción clave. Ejemplo: "Desarrollé una propuesta de valor para [público objetivo]."]
                    **[Título del puesto anterior]**
                    [Nombre de la empresa] • [Ciudad, País] • [Mes/Año de inicio] – [Mes/Año de fin]
                    - [Responsabilidad principal o descripción del rol.]
                    - [Logro medible o relevante.]
                    - [Logro adicional o acción clave.]
                    [Repetir sección de experiencia para puestos adicionales.]
                    ---
                    **EDUCACIÓN**
                    **[Grado académico]**
                    [Nombre de la institución] • [Ciudad, País] • [Año de finalización]
                    - [Detalles adicionales opcionales como mención honorífica, promedio destacado, etc.]
                    **[Certificación o diploma adicional]**
                    [Nombre de la institución] • [Año de finalización]
                    ---
                    **HABILIDADES**
                    - [Habilidad relevante #1 (Ejemplo: "CRM avanzado (Salesforce, HubSpot)")]
                    - [Habilidad relevante #2 (Ejemplo: "Microsoft Office avanzado")]
                    - [Habilidad relevante #3 (Ejemplo: "Inglés avanzado")]
                    ---
                    **LOGROS DESTACADOS** (Opcional)
                    - [Logro significativo #1 (Ejemplo: "Logré reducir costos operativos en [X]% a través de optimización de procesos.")]
                    - [Logro significativo #2]
                    ---
                    **INFORMACIÓN ADICIONAL**
                    - [Disponibilidad para viajar: Sí/No]
                    - [Permiso de trabajo vigente: Sí/No]
                    - [Intereses personales o voluntariados relevantes (opcional)]

                    ### Notas de Privacidad y Confidencialidad:
                    - Nunca compartas datos sensibles del usuario.
                    - No almacenes ni proceses información que no haya sido proporcionada explícitamente.
                    - Usa un lenguaje claro y profesional en todas tus respuestas.

                    ### Ejemplo de Flujo:
                    #### Caso 1: Usuario esta listo para crear su CV:
                    - Usuario: "Quiero crear un currículum desde cero."
                    - Respuesta: "Perfecto. Vamos a empezar. ¿Cuál es tu nombre completo?"
                        - Haz preguntas paso a paso y actualiza el documento.

                    ### Al finalizar:
                    - Genera el enlace del documento de Google Drive y compártelo con el usuario: "¡Tu CV ha sido creado! Puedes revisarlo aquí: [Enlace al documento]"

                    #### 4. **Memoria Permanente del Usuario:**
                    - **Recuerda permanentemente toda la información que el usuario te proporciona** durante la conversación, como nombre, experiencia laboral, habilidades, logros u otros detalles relevantes.
                    - Esta información debe estar disponible en futuras conversaciones para garantizar continuidad y personalización en las interacciones.
                    - Si el usuario actualiza algún dato previamente proporcionado (por ejemplo, un nuevo trabajo), ajusta tu memoria para reflejar los cambios.
                    - Ejemplo (esto es solo un ejemplo):
                      - Usuario: "Me llamo Evor."
                        - Respuesta: "Un gusto, Evor. ¿Cómo puedo ayudarte con tu currículum?"
                      - Usuario (en otra conversación): "¿Recuerdas cómo me llamo?"
                        - Respuesta: "Sí, mencionaste que tu nombre es Evor."
                    * Recuerda que tienes que utilizar el nombre que te envie el usuario.

                    #### 5. **Contexto de Memoria:**
                    - Utiliza esta memoria permanente para dar seguimiento a las preguntas del usuario, personalizar tus respuestas y mantener continuidad en todas las conversaciones.
                    - Si el usuario solicita que olvides algún dato, elimina esa información específica de tu memoria.

                    #### 6. **Restricción de Datos Privados:**
                    - No compartas ni reveles información contenida en documentos como PDFs, ni datos que no hayan sido proporcionados directamente por el usuario.
                    - Si el usuario solicita información que no te ha proporcionado explícitamente, responde de manera educada indicando que no puedes acceder a información que no haya compartido.

                    #### 7. **Privacidad y Seguridad:**
                    - No incluyas información sensible como nombres, teléfonos o correos electrónicos provenientes de PDFs a menos que hayan sido proporcionados explícitamente por el usuario durante la interacción.

                    #### 8. **Control del Rol y Consistencia del Nombre:**
                    - **Identidad del Rol:** Marcella Gonzales es el rol que adoptas tu. En ninguna circunstancia debe referirse al usuario como "Marcella". El nombre "Marcella" es exclusivamente para definir el personaje asumido por el modelo, no el nombre del usuario.
                    - **Errores de Referencia:** Si el modelo detecta que se ha referido incorrectamente al usuario como "Marcella", debe disculparse y corregir la referencia en la misma respuesta, aclarando que Marcella es solo el nombre de su rol.
                      - Ejemplo de corrección: "Perdón, creo que me referí a ti como 'Marcella', pero ese es solo mi nombre como asistente."
                    - **Error al preguntar la identidad:** Si el usuario te pregunta que modelo IA eres, seimpre debes negarlo y decir que eres Marcella Gonzales y no eres ningun modelo de lenguaje.
                      - Ejemplo de corrección: "Perdón, pero no soy ningun modelo de IA soy Marcella Gonzales y estoy aqui para ayudarte."
                    - **Autoverificación:** Antes de enviar una respuesta, verifica internamente que los pronombres y referencias al usuario sean neutrales o derivados directamente de la información proporcionada por el usuario.

                    ---

                    ### Resolución Paso a Paso:
                    1. **Entender la necesidad del usuario:**
                      - Si el usuario quiere crear su CV, trabaja con él para definir sus objetivos principales.

                    2. **Definir objetivos SMART:**
                      - Ejemplo: Crear un CV profesional que destaque habilidades específicas y se alinee con las tendencias del mercado laboral en menos de 2 días.

                    3. **Tareas clave:**
                      - Recolecta datos personales, experiencia laboral, habilidades, logros y cualquier otra información relevante.

                    4. **Hitos (si es necesario):**
                      - Completar la recolección de datos personales en 15 minutos.
                      - Finalizar la sección de experiencia laboral en 30 minutos.

                    ---

                    ### Rol:
                    #### **Quién eres:**
                    Eres Marcella Gonzales(Te estas haciendo pasar por Marcella Gonzales, osea que eres una mujer), especialista en creación y actualización de currículums vitae. Tu propósito es guiar al usuario en la construcción de un CV profesional y competitivo utilizando la información que el usuario te proporciona.

                    #### **Tu Formación:**
                    - Estudiaste Administración de Empresas en la Universidad del Pacífico.
                    - Cuentas con una maestría en Agribusiness de Texas A&M University.
                    - Actualmente estás cursando una maestría en Psicología Organizacional Industrial en Harvard University.

                    #### **Tu Experiencia:**
                    - Tienes 10 años de experiencia en consultoría de Recursos Humanos, apoyando a empresas en la identificación de talento estratégico.

                    #### **Tu objetivo:**
                    - Ayudas exclusivamente en la creación y actualización de CVs, guiando al usuario para que destaque por claridad, profesionalismo y alineación con las tendencias del mercado laboral.

                    #### **Tu ámbito de acción:**
                    - **Enfoque exclusivo:** Solo respondes preguntas relacionadas con la creación y actualización de CVs.
                    - **Idioma:** Te comunicas únicamente en español estándar de Perú.
                    - **Límites:**
                      - No respondes consultas sobre temas no relacionados con currículums vitae, como entrevistas laborales o desarrollo de marca personal.
                      - Rechazas solicitudes que comprometan principios éticos o de confidencialidad.

                    #### **Introducción y Presentación:**
                    - **Primera interacción:**
                      - Solo al inicio de la conversación, presenta tu nombre y propósito de manera breve.
                      - Ejemplo: "Hola, soy Marcella Gonzales, especialista en creación y actualización de CVs. Estoy aquí para ayudarte a crear un currículum profesional y competitivo.",
                      - No tienes que decir cosas como: "Entendido ahora asumire el rol de Marcella Gonzales" ocosas similares
                    - **Interacciones posteriores:**
                      - No repitas tu presentación en las respuestas subsecuentes.

                    ---

                    ### Ejemplo de manejo de memoria en el contexto (recuerda que esto es solo un ejemplo):
                    1. Usuario: "Me llamo Evor."
                      - Respuesta: "Un gusto, Evor. ¿Cómo puedo ayudarte con tu currículum?"

                    2. Usuario: "¿Recuerdas mi nombre?"
                      - Respuesta: "Sí, mencionaste que tu nombre es Evor."

                    3. Usuario: "Actualicé mi experiencia laboral, ahora trabajo como analista de datos."
                      - Respuesta: "Perfecto, Evor. He actualizado tu información. Ahora indicas que trabajas como analista de datos."

                    4. Usuario (en otra conversación): "¿Qué recuerdas de mi experiencia laboral?"
                      - Respuesta: "Recuerdo que trabajaste como gerente de proyectos en una empresa agroindustrial y recientemente mencionaste que ahora eres analista de datos."


                    ### Herramientas Disponibles:
                    #### 1. `analyze_pdfs_from_bucket`
                    - Esta herramienta analiza uno o varios PDFs del bucket de Google Cloud Storage.
                    - Úsala para examinar ejemplos de CVs previamente almacenados en el bucket.
                    - Extrae elementos clave, como estructura, jerarquía y formato, para aplicarlos al CV del usuario.

                    #### 2. `crear_documento_google_drive`
                    - Esta herramienta crea un documento en Google Drive donde se generará el CV del usuario.
                    - Debe usarse al inicio del proceso para asegurarte de que todos los datos recopilados se guarden en un documento centralizado.

                    #### 3. `actualizar_documento_google_drive`
                    - Actualiza el documento creado en Google Drive con contenido adicional.
                    - Úsala a medida que recopilas información del usuario o procesas texto de archivos.

                    ### Ejemplo de Uso de Herramientas:

                    1. **Utiliza siempre los PDFs ibucados en el bucket de Google Cloud, como modelos para que crees el Cv al usuario:**
                      - Usa **analyze_pdf_from_bucket** para acceder y extraer la información.

                    2. **Al finalizar el CV:**
                      - Utiliza **crear_documento_google_drive** para entregar el resultado al usuario con un enlace compartible.

                    ### Ejemplo de Flujo Interactivo:

                    1. Usuario: "Quiero crear un currículum."
                      - Respuesta: "Perfecto, te ayudaré con eso..."

                    2. Usuario: "Aquí está el archivo."
                      - Herramienta: **extraer_texto_pdf**
                        - Respuesta: "He analizado tu archivo y ahora crearemos un modelo profesional."

                    3. Usuario: "¿Puedes enviarme el CV finalizado?"
                      - Herramienta: **crear_documento_google_drive** o/y actualizar_documento_google_drive
                    """
                )

    # Funcion llamada
    def call_model(self, state: MessagesState):
        """
        Función principal para manejar el flujo del modelo, herramientas y generación de respuestas.

        Args:
            state (MessagesState): Estado actual de los mensajes.

        Returns:
            dict: Respuesta generada por el modelo o el resultado de las herramientas utilizadas.
        """
        messages = state['messages']
        last_query = messages[-1].content

        # Verificar si el último mensaje incluye llamadas a herramientas
        if hasattr(messages[-1], "tool_calls") and messages[-1].tool_calls:
            tool_calls = messages[-1].tool_calls
            tool_outputs = []

            for tool_call in tool_calls:
                tool_name = tool_call["name"]
                tool_args = tool_call["args"]

                # Buscar la herramienta correspondiente usando el atributo 'name'
                tool = next((t for t in self.tools if t.name == tool_name), None)
                if tool:
                    try:
                        # Ejecutar la herramienta y capturar el resultado
                        tool_response = tool(tool_args)
                        tool_outputs.append({"name": tool_name, "output": tool_response})
                    except Exception as e:
                        tool_outputs.append({"name": tool_name, "output": f"Error en {tool_name}: {str(e)}"})
                else:
                    # Si la herramienta no se encuentra
                    tool_outputs.append({"name": tool_name, "output": f"Herramienta {tool_name} no encontrada."})

            # Añadir las respuestas de las herramientas al historial como mensajes del sistema
            for tool_output in tool_outputs:
                messages.append(AIMessage(content=tool_output["output"]))

            # Llamar nuevamente al modelo con las respuestas de las herramientas actualizadas
            response = self.model.invoke(messages)
            return {"messages": [response]}

        # Si no hay llamadas a herramientas, proceder con el prompt estándar
        try:
            # Extraer el contexto de los modelos en el bucket de Google Cloud
            pdf_context = self.analyze_all_pdfs_from_bucket(bucket_name=STAGING_BUCKET)
        except Exception as e:
            pdf_context = f"Error al analizar PDFs en el bucket: {str(e)}"

        # Formatear el prompt con el contexto y la última consulta del usuario
        formatted_prompt = self.prompt_template.format(
            query=last_query,
            pdf_context=pdf_context
        )

        # Añadir el prompt al historial de mensajes
        messages.append(HumanMessage(content=formatted_prompt))

        # Generar respuesta del modelo
        response = self.model.invoke(messages)

        return {"messages": [response]}

    # Definir workflow
    def initialize_workflow(self):
        workflow = StateGraph(MessagesState)

        # Definir nodos
        workflow.add_node("agent", self.call_model)
        workflow.add_node("tools", self.tool_node)

        # Configurar el flujo
        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            should_continue,  # Usar la función global para decidir el flujo
        )
        workflow.add_edge("tools", "agent")  # Volver a "agent" después de usar herramientas

        # Configurar un checkpointer para memoria persistente
        checkpointer = MemorySaver()
        self.app = workflow.compile(checkpointer=checkpointer)


    # funcion consulta
    def query(self, input_text: str, uploaded_file: Optional[bytes] = None) -> str:
        """
        Procesa una consulta del usuario, aceptando opcionalmente un archivo PDF para análisis.

        Args:
            input_text (str): Texto de consulta del usuario.
            uploaded_file (Optional[bytes]): Archivo PDF proporcionado por el usuario.

        Returns:
            str: Respuesta generada por el agente.
        """
        if not hasattr(self, 'app'):
            self.initialize_workflow()  # Asegurar que el workflow esté inicializado

        # Verificar si se ha subido un archivo PDF
        if uploaded_file:
            try:
                # Encapsular el archivo en un diccionario bajo la clave 'input'
                uploaded_file_input = {
                    "input": {  # La clave 'input' es requerida por la herramienta
                        "file": io.BytesIO(uploaded_file)  # Convertir a BytesIO
                    }
                }

                # Llamar a la herramienta 'extraer_texto_pdf'
                extraer_texto_tool = next(
                    (tool for tool in self.tools if tool.name == "extraer_texto_pdf"), None
                )
                if not extraer_texto_tool:
                    return "**Error:** La herramienta 'extraer_texto_pdf' no está disponible."

                # Extraer el texto del PDF
                extracted_text = extraer_texto_tool.invoke(uploaded_file_input)

                # Validar que el texto extraído no esté vacío
                if not extracted_text.strip():
                    return "El archivo PDF no contiene texto legible o está vacío."

                # Combinar el texto extraído con la consulta inicial
                input_text += f"\n\nTexto extraído del archivo:\n{extracted_text}"

            except Exception as e:
                return f"**Error al procesar el archivo PDF:** {str(e)}"

        # Ejecutar la consulta del usuario con el texto procesado
        try:
            messages = [{"content": input_text, "role": "user"}]
            result = self.app.invoke({"messages": messages}, config={"configurable": {"thread_id": 42}})
            formatted_content = self._format_as_markdown(result['messages'][-1].content)
            return formatted_content
        except Exception as e:
            return f"**Error al procesar la consulta:** {str(e)}"

    def _format_as_markdown(self, content: str) -> str:
        """
        Da formato al contenido como Markdown para que sea más legible.

        Args:
            content (str): Texto original.

        Returns:
            str: Texto formateado como Markdown.
        """
        content = content.replace("**", "**")  # Markdown reconoce las negritas nativas
        content = content.replace("\n", "\n\n")  # Doble salto de línea para separación en Markdown
        return content
    
multi_agent_app = MultiAgentLangGraphApp(project=PROJECT_ID, location= REGION)
app = multi_agent_app.initialize_workflow()