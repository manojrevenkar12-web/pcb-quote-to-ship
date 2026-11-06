select machine, kind, failed, repair_start, repair_end,
       repair_end - repair_start as repair_minutes, repair_start - failed as waited_minutes,
       floor(failed / 1440)::int as day
from {{ ref('stg_downtime') }}
