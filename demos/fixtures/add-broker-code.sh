#!/usr/bin/env bash
# A teammate adds a broker_code column to the transaction model and its contract.
set -e
sql=transform/warehouse/models/transaction.sql
schema=transform/warehouse/models/schema.yml

# add the column to the model's SELECT (a real dbt column)
perl -0pi -e 's/(    counterparty_id\n)(from source\n-- mdl:generated-end)/$1    , cast(null as varchar) as broker_code\n$2/' "$sql"

# declare it in the enforced contract (so dbt knows its type)
python3 - "$schema" <<'PY'
import sys
f = sys.argv[1]
s = open(f).read()
i = s.find("- name: transaction\n")
j = s.find("columns:\n", i) + len("columns:\n")
block = ("      - name: broker_code\n"
         "        data_type: VARCHAR\n"
         "        meta:\n"
         "          mdl_ulid: 01M3XBROKERCODE0000000000X\n")
open(f, "w").write(s[:j] + block + s[j:])
PY
echo "warehouse: + transaction.broker_code (SQL + contract)"
