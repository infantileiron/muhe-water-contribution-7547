import {request,post} from './client';
import type {MeterBatch,MeterObservationSet,MeterSetDetail,MeterBatchResponse} from '../types/domain';

export const listBatches=()=>request<MeterBatch[]>('/meter/batches');
export const listSets=()=>request<MeterObservationSet[]>('/meter/sets');
export const setDetail=(id:string)=>request<MeterSetDetail>('/meter/sets/'+id);
export const createBatch=(data:unknown)=>post<MeterBatchResponse>('/meter/batches',data);
