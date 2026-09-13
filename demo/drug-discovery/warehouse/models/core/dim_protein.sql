{{ config(materialized='table') }}
select
    md5(cast(uniprot_id as varchar)) as protein_sk,
    cast(uniprot_id as varchar) as uniprot_id,
    name,
    length_aa
from {{ ref('stg_protein') }}
