# @Author: YChen1993
# @Date:   2021-12-26 16:12:56
# @Last Modified by:   YChen1993
# @Last Modified time: 2021-12-26 16:23:31
python main.py --data_name Beauty --augment_threshold 12 --augmentation_warm_up_epoches 80 \
--augment_type_for_short SIMRC --insert_rate 0.5 --substitute_rate 0.05 --sch_min 0.0005 --k 105600 --phi 0.7 --tune_dir cutshuffle --cutoff --gpu_id 1 --token_shuffle