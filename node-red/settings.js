/**
 * Node-RED v4 runtime configuration for WEG PLC500 supervisory architecture.
 * Aligns environment defaults with `.env.example` and precompiles the
 * Draft 2020-12 `DiagnosticoAtuacaoPLC500` JSON Schema into `functionGlobalContext`.
 */

const fs = require("fs");
const path = require("path");

const envDefaults = {
    PLC_HOST: "192.168.10.10",
    PLC_PORT: "502",
    PLC_UNIT_ID: "1",
    PLC_WATCHDOG_TIMEOUT_MS: "5000",
    PLC_MODBUS_MAP_VERSION: "100",
    PLC_SIM_API_URL: "http://plc-simulator:8001",
    POSTGRES_HOST: "postgres",
    POSTGRES_PORT: "5432",
    POSTGRES_DB: "plc500_telemetry",
    POSTGRES_USER: "plc500_user",
    POSTGRES_PASSWORD: "",
    ML_API_URL: "http://ml-api:8000",
    ML_WINDOW_SECONDS: "60",
    ML_BASELINE_MU: "12.0",
    ML_BASELINE_SIGMA: "2.5",
    ML_ZSCORE_EPS: "0.01",
    NR_POLL_INTERVAL_MS: "500",
    NR_ANALYSIS_INTERVAL_S: "60",
    NR_MODBUS_TIMEOUT_MS: "1500",
    NR_MODBUS_RECONNECT_MS: "2000",
    LLM_PROVIDER: "none",
    OPENAI_API_KEY: "",
    OPENAI_BASE_URL: "https://api.openai.com/v1",
    OPENAI_MODEL: "gpt-4o-mini",
    OLLAMA_BASE_URL: "http://host.docker.internal:11434",
    OLLAMA_MODEL: "llama3.1:8b",
    LLM_TIMEOUT_MS: "8000",
    TELEGRAM_BOT_TOKEN: "",
    TELEGRAM_CHAT_ID: ""
};

for (const [key, fallback] of Object.entries(envDefaults)) {
    if (process.env[key] === undefined) {
        process.env[key] = fallback;
    }
}

const schemaPath = path.join(__dirname, "schemas", "diagnostico_atuacao.schema.json");
let diagnosticoSchema = null;
let validateDiagnostico = null;
let ajvInstance = null;

try {
    diagnosticoSchema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
} catch (err) {
    console.warn("[PLC500] Warning: failed to read JSON schema at", schemaPath, err.message);
}

try {
    const Ajv2020 = require("ajv/dist/2020");
    const addFormats = require("ajv-formats");
    ajvInstance = new Ajv2020({ allErrors: true, strict: false });
    addFormats(ajvInstance);
    if (diagnosticoSchema) {
        validateDiagnostico = ajvInstance.compile(diagnosticoSchema);
    }
} catch (err) {
    console.warn("[PLC500] Warning: Ajv2020 not initialized (run npm install in node-red/):", err.message);
}

const plcConfig = {
    plcHost: process.env.PLC_HOST,
    plcPort: Number(process.env.PLC_PORT) || 502,
    unitId: Number(process.env.PLC_UNIT_ID || process.env.UNIT_ID) || 1,
    watchdogTimeoutMs: Number(process.env.PLC_WATCHDOG_TIMEOUT_MS) || 5000,
    modbusMapVersion: Number(process.env.PLC_MODBUS_MAP_VERSION) || 100,
    pollIntervalMs: Number(process.env.NR_POLL_INTERVAL_MS) || 500,
    analysisIntervalS: Number(process.env.NR_ANALYSIS_INTERVAL_S) || 60,
    windowSeconds: Number(process.env.ML_WINDOW_SECONDS) || 60,
    baselineMu: Number(process.env.ML_BASELINE_MU) || 12.0,
    baselineSigma: Number(process.env.ML_BASELINE_SIGMA) || 2.5,
    zscoreEps: Number(process.env.ML_ZSCORE_EPS) || 0.01,
    mlApiUrl: process.env.ML_API_URL,
    simApiUrl: process.env.PLC_SIM_API_URL,
    llmProvider: (process.env.LLM_PROVIDER || "none").toLowerCase(),
    openaiApiKey: process.env.OPENAI_API_KEY || "",
    openaiBaseUrl: process.env.OPENAI_BASE_URL,
    openaiModel: process.env.OPENAI_MODEL,
    ollamaBaseUrl: process.env.OLLAMA_BASE_URL,
    ollamaModel: process.env.OLLAMA_MODEL,
    llmTimeoutMs: Number(process.env.LLM_TIMEOUT_MS) || 8000,
    telegramBotToken: process.env.TELEGRAM_BOT_TOKEN || "",
    telegramChatId: process.env.TELEGRAM_CHAT_ID || ""
};

const settings = {
    flowFile: "flows.json",
    flowFilePretty: true,
    credentialSecret: process.env.NR_CREDENTIAL_SECRET || "CHANGE_ME_NODERED_CREDENTIAL_SECRET",
    uiPort: Number(process.env.NR_PORT || process.env.PORT) || 1880,
    mqttReconnectTime: 15000,
    serialReconnectTime: 15000,
    debugMaxLength: 1000,
    functionExternalModules: true,
    functionGlobalContext: {
        ajv: ajvInstance,
        diagnosticoSchema,
        validateDiagnostico,
        plcConfig
    },
    exportGlobalContextKeys: false,
    logging: {
        console: {
            level: (process.env.LOG_LEVEL || "info").toLowerCase(),
            metrics: false,
            audit: false
        }
    },
    editorTheme: {
        projects: {
            enabled: false
        },
        tours: false
    }
};

// Optional bcrypt-hashed adminAuth (enable explicitly via NR_ENABLE_AUTH=true + NR_ADMIN_PASSWORD_HASH)
if (process.env.NR_ENABLE_AUTH === "true" && process.env.NR_ADMIN_PASSWORD_HASH) {
    settings.adminAuth = {
        type: "credentials",
        users: [
            {
                username: process.env.NR_ADMIN_USER || "admin",
                password: process.env.NR_ADMIN_PASSWORD_HASH,
                permissions: "*"
            }
        ]
    };
}

module.exports = settings;
