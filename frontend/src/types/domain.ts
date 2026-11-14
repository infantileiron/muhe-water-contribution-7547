export interface User{email:string;role:string}
export interface Observation{zone:string;period:string;scale:number;water:number}
export interface Dataset{id:string;name:string;source:string;rows:Observation[];origin?:string;meter_set_id?:string|null;created_at:string}
export interface MeterReading{zone:string;read_at:string;cumulative:number}
export interface MeterPersonDay{zone:string;period:string;scale:number}
export interface MeterAllocation{period:string;seconds:number;time_share:number;water:number;scale:number|null;monthly_water:number|null}
export interface MeterSegment{zone:string;reading_from:MeterReading;reading_to:MeterReading;delta:number;span_seconds:number;allocations:MeterAllocation[]}
export interface MeterZoneReconciliation{zone:string;meter_total:number;monthly_total:number;residual:number}
export interface MeterObservationSet{id:string;batch_id:string;version:number;status:string;monthly:Observation[];segments:MeterSegment[];reconciliation:{meter_total:number;monthly_total:number;residual:number;by_zone:MeterZoneReconciliation[];tolerance:number};checksum:string;dataset_id:string;month_count:number;zone_count:number;batch_name?:string;source?:string;created_at:string}
export interface MeterBatch{id:string;idempotency_key:string;name:string;source:string;status:string;readings:MeterReading[];person_days:MeterPersonDay[];reading_count:number;person_day_count:number;observation_set?:MeterObservationSet;created_at:string}
export interface MeterSetDetail extends MeterObservationSet{batch:{id:string;name:string;source:string;readings:MeterReading[];person_days:MeterPersonDay[]};dataset_name:string;period_reconciliation:{period:string;water:number;scale:number;zones:number}[]}
export interface MeterBatchResponse{reused:boolean;observation_set:MeterObservationSet}
export interface Comparison{id:string;name:string;dataset_id:string;period_before:string;period_after:string;created_at:string}
export interface Factors{scale:number;structure:number;intensity:number}
export interface ZoneContribution extends Factors{zone:string}
export interface Decomposition{contributions:Factors;residual:number;zones:ZoneContribution[];method:string}
export interface Result{period_before:string;period_after:string;water_before:number;water_after:number;delta:number;scale_before:number;scale_after:number;share_sum_before:number;share_sum_after:number;shapley:Decomposition;lmdi:Decomposition;observations:{zone:string;scale_before:number;scale_after:number;share_before:number;share_after:number;intensity_before:number;intensity_after:number;water_before:number;water_after:number}[];checksum:string;assumptions:string[]}
export interface Run{id:string;comparison_id:string;status:string;result:Result;error:string;created_at:string}
export interface Review{id:string;run_id:string;note:string;decision:string;author:string;created_at:string}
export interface Report{id:string;run_id:string;title:string;published_by:string;created_at:string}
export interface Audit{id:string;actor:string;action:string;status:number;created_at:string}
