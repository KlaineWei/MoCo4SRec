# Reference

Please cite our paper if you use this code.

```

```

# Implementation
## Requirements

Python >= 3.7  
Pytorch >= 1.2.0  
tqdm == 4.26.0

## Datasets

Seven prepared datasets are included in `data` folder.

## Train Model

To train MoCo4SRec on `Sports_and_Outdoors` dataset, change to the `src` folder and run following command: 

```
bash sports.sh
```
You can train MoCo4SRec on other datasets in a similar way.

The script will automatically train MoCo4SRec and save the best model found in validation set, and then evaluate on test set


# Acknowledgement
 - Transformer and training pipeline are implemented based on [CoSeRec](https://github.com/YChen1993/CoSeRec). Thanks them for providing efficient implementation.

