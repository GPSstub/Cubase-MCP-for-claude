# Human read me

# Cubase Claude MCP

**Control your DAW with AI.**

Cubase Claude MCP는 AI 어시스턴트 Claude와 Steinberg Cubase를 연결하기 위한 오픈소스 MCP(Model Context Protocol) 통합 프로젝트입니다.

자연어 명령을 통해 Cubase의 다양한 기능을 제어하고, 음악 제작 및 오디오 엔지니어링 작업을 더욱 효율적으로 수행할 수 있도록 설계되었습니다.

복잡한 DAW 작업을 반복적인 마우스 조작 대신 AI와의 대화로 처리하는 것을 목표로 합니다.

> **프로젝트 상태:** 개발 중  
> **대상 DAW:** Steinberg Cubase 13  
> **AI 클라이언트:** Claude Code 및 MCP 호환 클라이언트  
> **주의:** 비공식 커뮤니티 프로젝트이며 Steinberg 또는 Anthropic과 제휴하지 않았습니다.

---

## Overview

Cubase Claude MCP는 Claude가 Cubase의 기능을 활용할 수 있도록 MCP 서버를 통해 제어 인터페이스를 제공합니다.

사용자는 자연어로 작업을 요청하고, Claude는 사용 가능한 MCP 도구를 호출하여 Cubase와 상호작용합니다.

### Architecture

```text
┌──────────────────────┐
│   Claude / Claude Code│
│   Natural Language    │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│     MCP Server       │
│  Command Processing  │
│  Tool Management     │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ Cubase Control Layer │
│ MIDI Remote / Bridge │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   Steinberg Cubase   │
│      DAW Engine      │
└──────────────────────┘
```

오디오 분석 기능은 필요에 따라 별도의 분석 엔진과 연동할 수 있습니다.

실제 통신 방식은 프로젝트 구현에 따라 달라집니다.

---

## Features

다음 항목은 프로젝트가 목표로 하는 기능입니다. 실제 지원 범위와 구현 상태는 릴리스별로 달라질 수 있습니다.

### DAW Control

- 프로젝트 재생 및 정지
- 녹음 제어
- 트랙 선택 및 탐색
- 믹서 볼륨 및 팬 조절
- 뮤트 및 솔로 제어
- 기본적인 프로젝트 상태 조회

### Plugin Control

- VST 플러그인 정보 조회
- 지원되는 플러그인 파라미터 읽기 및 변경
- EQ 및 컴프레서 설정 보조
- 플러그인 삽입 및 관리 기능 확장

플러그인 삽입 및 제어 범위는 Cubase API, 선택한 통신 방식, 플러그인 구조에 따라 제한될 수 있습니다.

### Audio Analysis

- 오디오 파일 파형 분석
- 피크 및 RMS 레벨 측정
- 주파수 스펙트럼 분석
- LUFS 기반 라우드니스 측정
- 클리핑 및 오디오 신호 문제 진단 지원

오디오 분석은 별도의 오디오 분석 엔진을 통해 구현할 수 있으며, Cubase 내부 오디오 버퍼에 대한 직접 접근을 의미하지 않습니다.

### AI-Assisted Workflow

Claude를 활용하여 믹싱 작업을 분석하고, 반복적인 설정 변경을 자동화하며, 자연어 기반의 오디오 작업 흐름을 구축하는 것을 목표로 합니다.

---

## Requirements

- Steinberg Cubase 13
- Claude Code 또는 호환 MCP 클라이언트
- 프로젝트 구현에 필요한 런타임 및 의존성
- Cubase와 MCP 서버 간 통신을 위한 구성 요소

**Compatibility Note**

Steinberg MIDI Remote API의 DirectAccess 기능은 Cubase 13.0.50 이상에서 지원됩니다. 해당 기능을 사용하는 구현에서는 호스트 버전을 확인해야 합니다.

---

## Installation

> 다음 설정은 일반적인 stdio 기반 MCP 서버의 구성 예시입니다. 실제 실행 명령과 설치 절차는 해당 프로젝트의 소스코드 및 릴리스에 맞게 변경해야 합니다.

### 1. Clone Repository

```bash
git clone <REPOSITORY_URL>
cd <PROJECT_DIRECTORY>
```

### 2. Install Dependencies

프로젝트에서 사용하는 프로그래밍 언어 및 패키지 관리자에 따라 필요한 의존성을 설치합니다.

### 3. Configure Claude Code

Claude Code에서 다음 형식으로 MCP 서버를 등록할 수 있습니다.

```bash
claude mcp add --transport stdio cubase-mcp -- <RUNTIME> <SERVER_ENTRY>
```

`<RUNTIME>`과 `<SERVER_ENTRY>`는 실제 서버 실행 명령과 진입 파일로 변경해야 합니다.

또는 프로젝트 루트의 `.mcp.json` 파일에 서버 설정을 추가할 수 있습니다.

```json
{
  "mcpServers": {
    "cubase-mcp": {
      "type": "stdio",
      "command": "<RUNTIME>",
      "args": ["<ABSOLUTE_SERVER_ENTRY_PATH>"]
    }
  }
}
```

Claude Code에서 `/mcp` 명령을 실행하여 서버 연결 상태를 확인합니다.

### 4. Connect Cubase

Cubase를 실행하고 프로젝트 구현에 필요한 MIDI Remote 스크립트 또는 통신 브리지를 활성화합니다.

연결이 완료되면 Claude에서 제공되는 MCP 도구를 확인할 수 있습니다.

---

## Usage Examples

다음은 목표 기능이 구현된 환경에서 사용할 수 있는 자연어 명령의 예시입니다.

**Basic Control**

- "현재 프로젝트 재생해 줘."
- "재생을 정지하고 선택한 트랙을 뮤트해 줘."
- "현재 선택된 트랙의 볼륨을 확인해 줘."

**Mixing**

- "선택한 베이스 트랙의 볼륨을 3dB 낮춰 줘."
- "기타 트랙에 적용된 EQ 파라미터를 확인해 줘."
- "현재 트랙의 컴프레서 설정을 분석해 줘."

**Audio Analysis**

- "이 오디오 파일의 주파수 분포를 분석해 줘."
- "피크 레벨과 LUFS를 측정해 줘."
- "클리핑이 발생하는 구간을 찾아 줘."

지원되지 않는 기능은 MCP 도구를 통해 실행할 수 없으며, AI의 분석 결과는 실제 프로젝트 데이터에 대한 접근 범위에 따라 달라질 수 있습니다.

---

## Limitations

Cubase Claude MCP는 Cubase의 공식 기능과 접근 가능한 인터페이스의 제한을 받습니다.

- 모든 Cubase 기능이 외부 API를 통해 제공되는 것은 아닙니다.
- 일부 플러그인은 파라미터 접근이나 자동화가 제한될 수 있습니다.
- 실시간 오디오 분석에는 별도 오디오 데이터 전달 및 처리 기능이 필요할 수 있습니다.
- Cubase 내부 파형 정보와 외부 파일 분석 결과는 다를 수 있습니다.
- GUI 자동화 방식은 창 상태, 화면 배치 및 애플리케이션 버전에 영향을 받을 수 있습니다.
- AI가 생성한 명령 또는 믹싱 설정이 항상 정확한 결과를 보장하지는 않습니다.

---

## Safety

이 프로젝트는 실제 DAW 프로젝트의 설정을 변경할 수 있습니다.

사용 전 프로젝트를 백업하고, 중요한 작업에서는 변경 사항을 직접 확인하는 것을 권장합니다.

특히 트랙 삭제, 플러그인 변경, 녹음 파일 덮어쓰기 등 복구하기 어려운 작업에는 별도의 확인 절차가 필요할 수 있습니다.

---

## Roadmap

- [ ] 안정적인 Cubase 연결 및 상태 확인
- [ ] 기본 Transport 제어
- [ ] 트랙 및 믹서 제어
- [ ] VST 파라미터 연동
- [ ] 플러그인 삽입 및 관리 기능
- [ ] 오디오 파일 분석 엔진
- [ ] 믹싱 워크플로 자동화
- [ ] 오류 처리 및 안전한 명령 실행
- [ ] 사용자 문서 및 테스트 확대

*체크박스는 공개 전 실제 구현 상태에 맞게 업데이트해야 합니다.*

---

## Contributing

버그 제보, 기능 제안, 문서 개선 및 코드 기여를 환영합니다.

문제가 발생하면 GitHub Issues에 재현 방법, Cubase 버전, 실행 환경, 오류 로그를 함께 첨부해 주세요.

## License

라이선스는 저장소의 `LICENSE` 파일을 참조하세요. 공개 전 사용할 라이선스를 지정해야 합니다.

## Disclaimer

Cubase Claude MCP is an independent, unofficial open-source project.

Cubase and VST are trademarks of Steinberg Media Technologies GmbH. Claude is a product of Anthropic.

This project is not affiliated with, endorsed by, or sponsored by Steinberg or Anthropic.
