{{ config(materialized='table') }}
select
    md5(cast(compound_id as varchar)) as compound_sk,
    cast(compound_id as varchar) as compound_id,
    smiles,
    molecular_weight
from {{ ref('stg_compound') }}
