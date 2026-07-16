# HarnessAgent module and third-party relationships

This document reflects the implementation and versioned configuration currently in
this repository. Solid arrows are runtime calls or imports; dashed arrows are
configuration, installation, optional, or test-only relationships.

## 1. Runtime architecture

```mermaid
flowchart LR
    Browser["Browser\nindex.html"]

    subgraph Client["Harness host - Windows or Linux"]
        Web["goose_web\nserver.py or server.ps1\nHTTP :8799"]
        WebCfg["goose_web/config.json"]
        Goose["Goose CLI\nagent orchestrator"]
        GooseCfg["Goose config.yaml\nprovider and extensions"]
        Workspace["workspace/\nuploads and agent files"]
        Builtins["Built-in extensions\ndeveloper, memory,\ncomputercontroller"]

        subgraph LocalMCP["Local FastMCP servers - streamable HTTP"]
            Diagnostic["12 read-only diagnostic MCPs\n127.0.0.1:8777-8788"]
            DtmSdk["DTM SDK MCP\n127.0.0.1:8789\nconfirmation-gated writes"]
            Obsidian["Obsidian vault MCP\n127.0.0.1:8790\nconfirmation-gated writes"]
        end
    end

    subgraph GB10["GB10 model and knowledge host"]
        Chat["vLLM OpenAI-compatible\nQwen3.6 chat :8000"]
        Embed["vLLM OpenAI-compatible\nQwen3 embedding :8001"]
        Ollama["Ollama fallback\nQwen3.5 :11434"]
        DtmProxy["DTM mcp-proxy :8765"]
        PkProxy["PK mcp-proxy :8766"]
        Knowledge["PersonalKnowledge agents\nand ChromaDB collections"]
    end

    Browser -->|"HTTP, NDJSON chat stream"| Web
    Browser -->|"upload"| Web
    Web -->|"goose run subprocess"| Goose
    Web -->|"read/write uploads"| Workspace
    Web -->|"health probes"| Chat
    Web -->|"health probes"| Embed
    Web -->|"health probes"| Ollama
    Web -->|"MCP initialize + tools/list\nfor live discovery"| LocalMCP
    Web -.->|"loads"| WebCfg
    Web -.->|"discovers/toggles extensions"| GooseCfg

    Goose -.->|"loads"| GooseCfg
    Goose --> Builtins
    Goose -->|"MCP streamable HTTP"| Diagnostic
    Goose -->|"MCP streamable HTTP"| DtmSdk
    Goose -->|"MCP streamable HTTP"| Obsidian
    Goose -->|"chat completions"| Chat
    Goose -.->|"fallback provider"| Ollama
    Goose -->|"MCP streamable HTTP"| DtmProxy
    Goose -->|"MCP streamable HTTP"| PkProxy
    DtmProxy --> Knowledge
    PkProxy --> Knowledge
    Knowledge -->|"embeddings"| Embed
```

## 2. Repository module relationships

The shared manifest drives installation and the non-privileged batch-test client:

```mermaid
flowchart LR
    Manifest["config/mcp_servers.json"] --> Setup["setup_mcp_servers.ps1"]
    Manifest --> Batch["test_mcp_servers.ps1\n+ Python test engine"]
    Batch --> Servers["14 local MCP servers"]
    Batch --> Reports["reports/mcp/*.json + *.md"]
```

```mermaid
flowchart TB
    Manifest["config/mcp_servers.json"]
    Setup["setup_mcp_servers.ps1"]
    Batch["test_mcp_servers.ps1\nand scripts/test_mcp_servers.py"]
    Reports["reports/mcp/\nJSON and Markdown"]
    Config["config/windows_config.yaml"]
    Tasks["Windows Scheduled Tasks\nAtLogOn, current user\nInteractive logon"]
    Launcher["scripts/start_mcp_hidden.ps1\nhidden PowerShell launcher"]
    Stdout["logs/mcp/<name>.stdout.log\n10 MiB, one .1 generation"]
    Stderr["logs/mcp/<name>.stderr.log\n10 MiB, one .1 generation"]
    Goose["Goose CLI"]

    Manifest -.->|"server metadata"| Setup
    Manifest -.->|"test targets and health tools"| Batch
    Setup -.->|"pip install requirements"| PyDeps["Python dependencies"]
    Setup -.->|"register"| Tasks
    Setup -.->|"immediate hidden start\ninherits setup token"| Launcher
    Setup -.->|"register extensions"| Config
    Config -.-> Goose
    Tasks -->|"hidden PowerShell action"| Launcher
    Launcher -->|"run Python MCP"| Servers
    Launcher -->|"append/rotate"| Stdout
    Launcher -->|"append/rotate"| Stderr
    Goose -->|"MCP over 127.0.0.1"| Servers
    Batch -->|"initialize, list, health call"| Servers
    Batch -->|"write"| Reports

    subgraph Servers["FastMCP server entry points and imported repository modules"]
        SRUM["srum_mcp_server.py :8777"] --> SRUMLive["live_metrics.py\npsutil + WMI"]
        SRUM --> SRUMReader["srum_reader.py\nesentutl + dissect.esedb"]

        Event["eventlog_mcp_server.py :8778"] --> EventReader["eventlog_reader.py\npywin32 Event Log API"]
        Event --> Curated["curated.py"] --> EventReader

        Crash["crash_mcp_server.py :8779"] --> WER["wer_reader.py"]
        Crash --> Dump["dump_reader.py\noptional CDB analysis"]
        WER --> Bugchecks["bugchecks.py"]
        Dump --> Bugchecks

        Exec["exec_mcp_server.py :8780"] --> Prefetch["prefetch_reader.py"]
        Exec --> Registry["registry_forensics.py\nBAM, UserAssist, ShimCache"]

        Drift["drift_mcp_server.py :8781"] --> DriftStore["drift_store.py\nJSON + SQLite snapshots"]
        DriftStore --> Collectors["collectors.py\nautoruns, services, programs, tasks"]

        Net["netconn_mcp_server.py :8782"] --> NetReader["netconn_reader.py\npsutil + tasklist"]

        Perf["perfmon_mcp_server.py :8783"] --> PDH["pdh_reader.py\nWindows PDH API"]

        Disk["disk_mcp_server.py :8784"] --> USN["usn_reader.py\nDeviceIoControl / USN journal"]
        Disk --> DiskHealth["disk_health.py\nStorage cmdlets + fsutil"]

        Proc["procinspect_mcp_server.py :8785"] --> ProcNative["native.py\nRestart Manager + Wait Chain"]
        Proc --> ProcDetail["procdetail.py\npsutil + Authenticode"]

        Mem["memstate_mcp_server.py :8786"] --> MemNative["native.py\nNT/PSAPI memory APIs"]
        Mem --> PoolTags["pooltags.py\npool tag and driver mapping"]

        Filter["filterstack_mcp_server.py :8787"] --> Parsers["parsers.py\nfltmc + NDIS/Winsock queries"]

        Update["winupdate_mcp_server.py :8788"] --> UpdateReader["winupdate.py\nWindows Update COM + registry"]

        SDK["dtm_sdk_mcp_server.py :8789"] --> SDKConfig["config.py"]
        SDK --> DataTypes["datatypes.py"]
        SDK --> HowTo["howto.py"]
        SDK --> Policy["policy.py\nconfirmation tokens"]
        SDK --> Runner["runner.py\nDTP utility subprocesses"]

        Obs["obsidian_mcp_server.py :8790"] --> ObsConfig["config.py"]
        Obs --> Index["index.py\nsearch and link graph"]
        Index --> Vault["vault.py\nconfined Markdown access"]
        Obs --> Vault
        Obs --> Tokens["tokens.py\nconfirmation tokens"]
    end
```

The immediate-start edge inherits the setup process token. Because suite setup runs elevated, an
install-time Obsidian process is elevated even though its Scheduled Task remains `RunLevel Limited`.
Obsidian returns to an unelevated token when restarted through that task or at the next logon.

## 3. Third-party software relationships

```mermaid
flowchart LR
    subgraph Repo["HarnessAgent components"]
        Setup["Setup and launch scripts"]
        Web["goose_web"]
        Goose["Goose runtime integration"]
        MCPs["14 local MCP servers"]
        EventMCP["eventlog MCP"]
        CrashMCP["crash MCP"]
        SRUMMCP["srum MCP"]
        DtmSdkMCP["dtmsdk MCP"]
        ObsidianMCP["obsidian MCP"]
        Tests["Test suites"]
    end

    subgraph Runtime["Third-party runtimes and platforms"]
        GooseCLI["Goose CLI"]
        Python["Python 3"]
        PowerShell["PowerShell / Windows PowerShell"]
        WinTask["Windows Task Scheduler"]
        Docker["Docker Compose"]
        Nvidia["NVIDIA container runtime and GPU driver"]
    end

    subgraph PythonPkgs["PyPI packages"]
        MCP["mcp >= 1.2\nFastMCP"]
        AnyIO["anyio >= 4.5"]
        YAML["PyYAML >= 6.0"]
        Psutil["psutil >= 5.9"]
        PyWin32["pywin32 >= 306"]
        ESE["dissect.esedb >= 3.0"]
        WMI["wmi >= 1.5"]
        Pytest["pytest >= 8.0"]
    end

    subgraph WindowsSW["Windows and vendor software"]
        Sysmon["Microsoft Sysinternals Sysmon\noptional telemetry enrichment"]
        WinAPIs["Windows APIs and tools\nctypes DLLs, PDH, registry, COM,\nfltmc, fsutil, esentutl, tasklist"]
        CDB["WinDbg CDB\noptional dump deep analysis"]
        TechHub["Dell TechHub service"]
        DTP["Dell DTP Sample/SDK utilities"]
        VaultFiles["Obsidian vault Markdown files\nObsidian app not required"]
    end

    subgraph ModelSW["GB10 model stack"]
        VLLM["vLLM OpenAI server container"]
        Ollama["Ollama fallback"]
        QwenChat["Qwen3.6-35B-A3B-FP8"]
        QwenEmbed["Qwen3-Embedding-4B"]
        QwenFallback["Qwen3.5 9B"]
        HF["Hugging Face Hub/cache"]
    end

    Setup --> PowerShell
    Setup --> Python
    Setup --> WinTask
    Web --> Python
    Web --> PowerShell
    Goose --> GooseCLI
    MCPs --> Python
    MCPs --> MCP
    MCPs --> WinAPIs
    MCPs --> EventMCP
    MCPs --> CrashMCP
    MCPs --> SRUMMCP
    MCPs --> DtmSdkMCP
    MCPs --> ObsidianMCP
    DtmSdkMCP --> AnyIO
    DtmSdkMCP --> YAML
    DtmSdkMCP --> TechHub
    DtmSdkMCP --> DTP
    SRUMMCP --> Psutil
    SRUMMCP --> ESE
    SRUMMCP --> WMI
    EventMCP --> PyWin32
    EventMCP -.->|"reads Operational channel"| Sysmon
    CrashMCP -.-> CDB
    MCPs --> Psutil
    MCPs --> PyWin32
    MCPs --> YAML
    Tests -.-> Pytest
    ObsidianMCP --> VaultFiles

    Docker --> Nvidia
    Docker --> VLLM
    VLLM --> QwenChat
    VLLM --> QwenEmbed
    VLLM --> HF
    Ollama --> QwenFallback
    GooseCLI -->|"OpenAI-compatible HTTP"| VLLM
    GooseCLI -.->|"fallback"| Ollama
```

Notes:

- `goose_web/server.py` is standard-library only. Its PowerShell counterpart uses
  .NET `HttpListener`; these are alternative implementations of the same HTTP API.
- All local MCPs use the `mcp` Python SDK's `FastMCP` and streamable HTTP. Package
  edges above are aggregated: only SRUM, netconn, and procinspect use `psutil`;
  eventlog, exec, and crash declare `pywin32`; DTM SDK and Obsidian use `PyYAML`.
- `pytest` is a development/test dependency rather than a production runtime dependency.
- Sysmon, CDB, Ollama, and the Obsidian desktop application are not required for the
  basic harness path. Sysmon enriches Event Log data, CDB deepens dump analysis, Ollama
  is the configured fallback, and the Obsidian MCP operates directly on vault files.
