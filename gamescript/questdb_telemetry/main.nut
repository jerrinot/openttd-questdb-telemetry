/*
 * QuestDB Telemetry - main GameScript loop.
 *
 * Runs as a "deity" (no company), so it can read EVERY company's vehicles and
 * economy. Each sweep (~every SLEEP_TICKS in-game ticks) it emits two kinds of
 * JSON messages to the admin port via GSAdmin.Send():
 *
 *   Vehicle telemetry:
 *     { "v": [ <vrec>, ... ], "y": year, "mo": month, "dy": day }
 *   Company economy:
 *     { "c": [ <crec>, ... ], "y": year, "mo": month, "dy": day }
 *
 * Records are positional arrays to stay under the 1450-byte GSAdmin.Send limit.
 * The Rust bridge knows these exact orders.
 *
 *   <vrec> = [ vid, vtype, owner, x, y,
 *              speed, max_speed, reliability, state,
 *              age, max_age, profit, profit_last, value,
 *              cargo_cap, cargo_load, running_cost ]
 *     vtype : 0=rail 1=road 2=water 3=air
 *     state : 0=running 1=stopped 2=depot 3=station 4=broken 5=crashed
 *     speed/max_speed : OpenTTD internal units (bridge converts to km/h)
 *
 *   <crec> = [ cid, money, company_value, income, expenses, performance, cargo_delivered, name ]
 *     income/expenses/cargo_delivered : current quarter (expenses is negative)
 *     performance : previous completed quarter (0-1000); 0 for a brand-new
 *                   company (the API does not return -1 for valid companies)
 *     name : the in-game company name (appended last for back-compat)
 */

class QuestDBTelemetry extends GSController {
	function Start();
}

function QuestDBTelemetry::Start()
{
	// How often to sample (in-game ticks). ~37 ticks per real second.
	local SLEEP_TICKS = 20;
	// Records per admin message. GSAdmin.Send DROPS (and on debug builds asserts)
	// any message whose JSON exceeds ~1450 bytes, and its return value does NOT
	// signal this — so keeping batches small is the only real guard. Records grow
	// as late-game Money values gain digits; these leave comfortable headroom.
	local BATCH = 10;    // vehicles per message
	local CBATCH = 6;    // companies per message

	GSLog.Info("QuestDB telemetry GameScript started - streaming vehicle + economy data to the admin port.");

	// Cargo types are fixed for the game; enumerate once for utilisation sums.
	local cargoes = [];
	foreach (cargo, _ in GSCargoList()) {
		cargoes.append(cargo);
	}

	while (true) {
		local date  = GSDate.GetCurrentDate();
		local year  = GSDate.GetYear(date);
		local month = GSDate.GetMonth(date);
		local day   = GSDate.GetDayOfMonth(date);

		// ---- company economy (batched) ------------------------------------
		local comp = [];
		for (local c = 0; c < 15; c++) {
			if (GSCompany.ResolveCompanyID(c) == GSCompany.COMPANY_INVALID) continue;
			local cname = GSCompany.GetName(c);
			if (cname == null) cname = "Company " + (c + 1);
			comp.append([
				c,
				GSCompany.GetBankBalance(c),
				GSCompany.GetQuarterlyCompanyValue(c, GSCompany.CURRENT_QUARTER),
				GSCompany.GetQuarterlyIncome(c, GSCompany.CURRENT_QUARTER),
				GSCompany.GetQuarterlyExpenses(c, GSCompany.CURRENT_QUARTER),
				GSCompany.GetQuarterlyPerformanceRating(c, GSCompany.CURRENT_QUARTER + 1),
				GSCompany.GetQuarterlyCargoDelivered(c, GSCompany.CURRENT_QUARTER),
				cname,
			]);
			if (comp.len() >= CBATCH) {
				GSAdmin.Send({ c = comp, y = year, mo = month, dy = day });
				comp = [];
			}
		}
		if (comp.len() > 0) {
			GSAdmin.Send({ c = comp, y = year, mo = month, dy = day });
		}

		// ---- vehicle telemetry (batched) ----------------------------------
		local batch = [];

		foreach (vid, _ in GSVehicleList()) {
			if (!GSVehicle.IsValidVehicle(vid)) continue;
			if (!GSVehicle.IsPrimaryVehicle(vid)) continue;

			local tile = GSVehicle.GetLocation(vid);
			if (!GSMap.IsValidTile(tile)) continue;

			local engine = GSVehicle.GetEngineType(vid);
			// max_speed is the engine's DESIGN max (an upper bound), not the
			// realised consist/railtype-limited speed.
			local max_speed = GSEngine.IsValidEngine(engine) ? GSEngine.GetMaxSpeed(engine) : 0;
			// Vehicle-scoped running cost (whole consist), not the lead engine's.
			local run_cost  = GSVehicle.GetRunningCost(vid);

			// Cargo utilisation: sum capacity + load across all cargo types.
			local cap = 0;
			local load = 0;
			foreach (cargo in cargoes) {
				cap  += GSVehicle.GetCapacity(vid, cargo);
				load += GSVehicle.GetCargoLoad(vid, cargo);
			}

			batch.append([
				vid,
				GSVehicle.GetVehicleType(vid),
				GSVehicle.GetOwner(vid),
				GSMap.GetTileX(tile),
				GSMap.GetTileY(tile),
				GSVehicle.GetCurrentSpeed(vid),
				max_speed,
				GSVehicle.GetReliability(vid),
				GSVehicle.GetState(vid),
				GSVehicle.GetAge(vid),
				GSVehicle.GetMaxAge(vid),
				GSVehicle.GetProfitThisYear(vid),
				GSVehicle.GetProfitLastYear(vid),
				GSVehicle.GetCurrentValue(vid),
				cap,
				load,
				run_cost,
			]);

			if (batch.len() >= BATCH) {
				GSAdmin.Send({ v = batch, y = year, mo = month, dy = day });
				batch = [];
			}
		}

		if (batch.len() > 0) {
			GSAdmin.Send({ v = batch, y = year, mo = month, dy = day });
		}

		this.Sleep(SLEEP_TICKS);
	}
}
