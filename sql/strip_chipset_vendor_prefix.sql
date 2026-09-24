-- Optional one-off. Only needed if you have NOT already run the
-- "strip Intel/AMD prefix" UPDATE from chat. Safe to run either way:
-- it only touches the 5 rows filled in manually, and only if they still
-- carry the prefix. (The Z390 socket fix for ids 282/326/339 is NOT here --
-- backfill_sockets.py handles those automatically in v3.8.6.)
UPDATE hardware_specs
SET mobo_chipset = TRIM(REGEXP_REPLACE(mobo_chipset, '^(Intel|AMD)[[:space:]]+', ''))
WHERE id IN (276, 277, 284, 321, 352) AND mobo_chipset REGEXP '^(Intel|AMD) ';

SELECT id, manufacturer, LEFT(model,40) AS model, mobo_socket, mobo_chipset
FROM hardware_specs WHERE id IN (276, 277, 284, 321, 352) ORDER BY id;
