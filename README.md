# Reference

Please cite our paper if you use this code.

```
@article{wei2023moco4srec,
  title={MoCo4SRec: A momentum contrastive learning framework for sequential recommendation},
  author={Wei, Zihan and Wu, Ning and Li, Fengxia and Wang, Ke and Zhang, Wei},
  journal={Expert Systems with Applications},
  pages={119911},
  year={2023},
  publisher={Elsevier}
}
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

