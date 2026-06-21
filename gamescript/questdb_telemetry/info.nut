/*
 * QuestDB Telemetry - OpenTTD GameScript.
 *
 * Collects live vehicle telemetry and pushes it to admin-port clients via
 * GSAdmin.Send(). It does NOT touch disk or network itself (GameScripts are
 * sandboxed) - the companion Rust bridge connects to the admin port and relays
 * the data into QuestDB.
 *
 * IMPORTANT: GetName() below MUST match the key used in the [game_scripts]
 * section of the OpenTTD config (see config/demo.cfg).
 */
class QuestDBTelemetry extends GSInfo {
	function GetAuthor()      { return "QuestDB Demo"; }
	function GetName()        { return "QuestDBTelemetry"; }
	function GetShortName()   { return "QDBT"; }
	function GetDescription() { return "Streams live vehicle telemetry (position, speed, profit) to the OpenTTD admin port for ingestion into QuestDB."; }
	function GetVersion()     { return 1; }
	function GetDate()        { return "2026-06-21"; }
	function GetAPIVersion()  { return "15"; }
	function CreateInstance() { return "QuestDBTelemetry"; }
	function GetURL()         { return "https://questdb.io"; }
}

RegisterGS(QuestDBTelemetry());
