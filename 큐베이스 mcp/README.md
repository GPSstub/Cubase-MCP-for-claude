# Cubase MCP (Cubase 13)

Claude가 Cubase를 읽고 조작할 수 있게 해 주는 MCP 서버입니다. 다룰 수 있는 범위:
- 모든 믹서 채널
- 선택된 트랙의 인서트 플러그인(서드파티 VST3 포함)과 파라미터, 센드
- 트랜스포트, 템포, 로케이터
- Cubase 키 커맨드 전체 (약 2,000개)
- 오디오 파일 분석

```
Claude ⇄ server/cubase_mcp.py ⇄ loopMIDI 포트 2개 ⇄ mcp_bridge.js (MIDI Remote) ⇄ Cubase
```

## 설치 상태 (이 PC)

| 항목 | 상태 |
|---|---|
| loopMIDI | 설치됨. SysEx 버퍼를 4096으로 늘림 |
| loopMIDI 포트 `CubaseMCP_ToCubase`, `CubaseMCP_FromCubase` | **직접 추가 필요** |
| 브리지 스크립트 | `Documents\Steinberg\Cubase\MIDI Remote\Driver Scripts\Local\mcp\bridge\mcp_bridge.js` |
| Python 환경 | `C:\cubase-mcp-venv` |
| Claude Code 등록 | `~/.claude.json`의 `mcpServers.cubase` (백업: `.claude.json.bak-cubase-mcp`) |

## 처음 연결하기

1. **loopMIDI 포트 추가:** loopMIDI 창 아래 칸에 `CubaseMCP_ToCubase`를 입력하고 **+**를 누릅니다. `CubaseMCP_FromCubase`도 같은 방법으로 추가합니다. 트레이 아이콘을 우클릭해서 **Autostart**를 켜 두면 재부팅 후에도 포트가 유지됩니다.
2. **Cubase 포트 설정:** Studio → Studio Setup → MIDI Port Setup에서 두 포트의 **"In 'All MIDI Inputs'"** 체크를 해제합니다.
3. **스크립트 불러오기:** 하단 Lower Zone의 MIDI Remote 탭에서 **Reload Scripts**를 누릅니다. "MCP Bridge"가 자동으로 잡혀야 합니다. 안 잡히면 **+ Add Surface** → MCP / Bridge를 선택합니다.
4. **Claude 새 세션 열기:** 새로 연 세션부터 `cubase` 도구가 보입니다.

스크립트 오류는 Studio → MIDI Remote Manager → Scripting Tools → Script Console에서 확인할 수 있습니다.

## 키 커맨드 갱신

Cubase에서 키 커맨드를 바꾼 뒤에는 아래 명령을 실행하고, Cubase에서 **Reload Scripts**를 누르세요.

```powershell
C:\cubase-mcp-venv\Scripts\python.exe tools\build_bridge.py --install
```

`cubase/mcp_bridge.template.js`가 원본이고, `mcp_bridge.js`는 위 명령으로 생성되는 파일입니다.

## 도구 목록

| 도구 | 설명 |
|---|---|
| `cubase_status` | 선택된 트랙, 센드, 포커스된 플러그인과 파라미터, 트랜스포트, 위치, 템포 |
| `list_tracks` | MixConsole의 모든 채널 (볼륨, 팬, 뮤트, 솔로, 녹음 대기, 모니터) |
| `select_track` | 이름으로 트랙 선택, 또는 다음/이전 트랙으로 이동 |
| `set_track` / `set_track_volume_db` | 트랙 이름으로 믹서 값 설정, 볼륨은 dB로도 지정 가능 |
| `set_send` | 선택된 트랙의 센드 레벨(정규화값 또는 dB)과 On/Off |
| `list_inserts` / `select_insert` | 인서트 플러그인 목록과 포커스 |
| `get_params` / `set_param` / `set_param_display` | 플러그인 파라미터 읽기, 정규화값으로 설정, 표시값(dB, Hz)으로 설정 |
| `set_insert` | 인서트 On/Off, 바이패스, 플러그인 창 열기 |
| `transport` | 재생/정지, 녹음, 사이클, 메트로놈, 위치, 사이클 구간, 템포 |
| `search_commands` / `run_command` | Cubase 키 커맨드 전체 검색과 실행 |
| `list_audio_files` / `analyze_audio` | 오디오 파일 분석: LUFS, 피크, 트루피크, 대역별 에너지, 스테레오 상관도, 템포, 키 |
| `render_and_analyze` | 선택한 트랙을 플러그인 포함해서 Render in Place로 렌더하고 분석, 레퍼런스를 주면 비교까지. 끝나면 렌더를 Undo로 정리(파일은 남음) |
| `compare_audio` | 레퍼런스와 내 소리를 1/3옥타브 단위로 비교: 대역별 차이, 추천 EQ(주파수, 모양, 게인, Q), 압축 조언, 밝기, 라우드니스. 구간(초)과 분석 범위 지정 가능 |

## 한계

- MIDI Remote API로는 아래 작업을 할 수 없습니다.
  - 특정 플러그인을 이름으로 삽입
  - 트랙 입출력 라우팅 지정
  - 클립, 오토메이션, MIDI 노트 편집 데이터 읽기
- 키 커맨드 중 대화상자를 여는 것(예: Add Track)은 대화상자까지만 열어 줍니다.
- 키 커맨드 실행은 단축키를 누른 것과 같아서, Cubase의 현재 선택이나 포커스 대상에 적용됩니다.
- 실제 Cubase에서 아직 테스트하지 않았습니다. API 이름은 공식 정의 파일과 대조해 확인했습니다.
