python main.py --data_name Yelp --augmentation_warm_up_epoches 300 --insert_rate 0.5 --sch_min 0.00099 --k 163200 --phi 0.71 --tune_dir cutshufflenoise --cutoff --token_shuffle --guassian_noise --gpu_id 1
python main.py --data_name Yelp --augmentation_warm_up_epoches 300 --insert_rate 0.5 --sch_min 0.00099 --k 163200 --phi 0.71 --tune_dir cut --cutoff --gpu_id 1
python main.py --data_name Yelp --augmentation_warm_up_epoches 300 --insert_rate 0.5 --sch_min 0.00099 --k 163200 --phi 0.71 --tune_dir base --gpu_id 1